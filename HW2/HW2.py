import taichi as ti
import time
import math

# ti.core.toggle_advance_optimization(False)
# ti.init(debug=True)
ti.init(arch=ti.gpu)

n_dim = 2
dt    = 1e-2
ht    = dt * 0.5 # half of dt
max_num_particles = 256
connection_radius = 0.9

bottom_y      = 0.05
top_y         = 0.95
left_x        = 0.05
right_x       = 0.95

gravity          = ti.Vector.field(n_dim, ti.f32, shape=())
num_particles    = ti.field(ti.i32, shape=())
spring_stiffness = ti.field(ti.f32, shape=())
particle_mass    = ti.field(ti.f32, shape=())
damping          = ti.field(ti.f32, shape=())
paused           = ti.field(ti.i32, shape=())


# rest_length[i, j] = 0 means i and j are not connected
rest_length  = ti.field(ti.f32, shape=(max_num_particles, max_num_particles))
deform_length  = ti.field(ti.f32, shape=(max_num_particles, max_num_particles))
position     = ti.Vector.field(n_dim, dtype=ti.f32, shape=max_num_particles) # position
velocity     = ti.Vector.field(n_dim, dtype=ti.f32, shape=max_num_particles) # velocity
new_velocity = ti.Vector.field(n_dim, dtype=ti.f32, shape=max_num_particles)
force        = ti.Vector.field(n_dim, dtype=ti.f32, shape=max_num_particles) # force 
new_force    = ti.Vector.field(n_dim, dtype=ti.f32, shape=max_num_particles) 

# A @ new_velocity = b
A      = ti.Matrix.field(n_dim, n_dim, dtype=ti.f32, shape=(max_num_particles, max_num_particles))
b      = ti.Vector.field(n_dim, dtype=ti.f32, shape=max_num_particles)
r      = ti.Vector.field(n_dim, dtype=ti.f32, shape=max_num_particles)
new_dv = ti.Vector.field(n_dim, dtype=ti.f32, shape=max_num_particles)

resi_out = ti.field(ti.f32,shape=(2))

gravity[None]          = [0, 0] # -9.8]
num_particles[None]    = 0
particle_mass[None]    = 1
damping[None]          = 0
spring_stiffness[None] = 100
paused[None]           = False

@ti.func
def iterate():
    n = num_particles[None]
    for i in range(n):
        r[i] = b[i] * 1.0
        
        for j in range(n):
            if i != j:
                r[i] -= A[i, j] @ new_velocity[j]
                
        # new_velocity[i] = A[i, i].inverse() @ r[i]
        new_dv[i] = A[i, i].inverse() @ r[i]
        # new_dv[i].x = (r[i].x - A[i,i][0,1] * new_velocity[i].y) / A[i, i][0,0]
        # new_dv[i].y = (r[i].y - A[i,i][1,0] * new_velocity[i].x) / A[i, i][1,1]
        
    for i in range(n):
        new_velocity[i] = new_dv[i] * 1.0


@ti.func
def resi() -> ti.f32:
    n = num_particles[None]
    res = 0.0
    
    for i in range(n):
        r[i] = b[i] * 1.0
        for j in range(n):
            r[i] -= A[i, j] @ new_velocity[j]
        res += r[i].x ** 2 
        res += r[i].y ** 2

    return res

@ti.kernel
def residual() -> ti.f32:
    return resi()

@ti.func
def solve_equation() :
    # for no_loop in range(1) :
    for i in range(1000) :
        iterate()
        # print("----->----->",resi())
        resi_out[0] = resi()
        resi_out[1] = i
        # print("----->----->",resi_out[0])
        if resi_out[0] < 1e-12 :
            break

@ti.kernel
def substep_explict():
    # Compute force and new velocity
    n = num_particles[None]
    for i in range(n):
        velocity[i] *= ti.exp(-dt * damping[None]) # damping
        total_force = gravity * particle_mass[None]
        for j in range(n):
            if rest_length[i, j] != 0:
                x_ij = position[i] - position[j]
                total_force += -spring_stiffness[None] * (x_ij.norm() - rest_length[i, j]) * x_ij.normalized()
        velocity[i] += dt * total_force / particle_mass[None]
        
    # Collide with ground
    for i in range(n):
        if position[i].y < bottom_y:
            position[i].y = bottom_y
            velocity[i].y = 0


    # Compute new position
    for i in range(num_particles[None]):
        position[i] += velocity[i] * dt


@ti.func
def f_ij(i,j) :
    x_ij = position[j] - position[i] # from i to j
    x_d  = x_ij.normalized()
    x_n  = x_ij.norm()
    return spring_stiffness[None] * (x_n - rest_length[i, j]) * x_d

# (1 - l/xn) * (xj - xi)

# d_f/d_xi
# -[(1 - l/xn), 0] - x * l * [ x / xn^3, y / xn^3 ]
# -[0, (1 - l/xn)] - y * l * [ x / xn^3, y / xn^3 ]

# d_f/d_xj
# [(1 - l/xn), 0] + x * l * [ x / xn^3, y / xn^3 ]
# [0, (1 - l/xn)] + y * l * [ x / xn^3, y / xn^3 ]

# I * (1 - l/xn) + l/xn * x_o
# I + l/nx * (x_o - I)

# df / dx
@ti.func
def dfj_ij(i,j) :
    x_ij = position[j] - position[i] # from i to j
    x_d  = x_ij.normalized()
    x_n  = x_ij.norm()
    x_o  = x_d.outer_product(x_d)
    x_e = ti.Matrix([[1,0],[0,1]])
    l = rest_length[i,j]
    res =  x_e + l / x_n * (x_o - x_e)
    return spring_stiffness[None] * res

# df / dv
@ti.func
def df_ij_v(i,j) :
    x_ij = position[j] - position[i] # from i to j
    x_d  = x_ij.normalized()
    x_n  = x_ij.norm()
    x_o  = x_d.outer_product(x_d)
    x_oi = x_o - [[1,0],[0,1]] 
    return - spring_stiffness[None] * ((x_n - rest_length[i,j]) / x_n * x_oi - x_o)

@ti.func
def collide_box() :
    n = num_particles[None]
    # Collide with box
    for i in range(n):
        if position[i].y < bottom_y:
            position[i].y  = bottom_y
            # new_force[i].y = 0 # abs(new_force[i].y)
            if velocity[i].y < 0 :
                velocity[i].y  = abs(velocity[i].y)
            # velocity[i].y = 0
        if position[i].y > top_y:
            position[i].y  = top_y
            # new_force[i].y = 0 # -abs(new_force[i].y)
            if velocity[i].y > 0 :
                velocity[i].y  = 0 # -abs(velocity[i].y)
            # velocity[i].y = 0
        if position[i].x < left_x:
            position[i].x  = left_x
            # if new_force[i].x < 0 :
            #     new_force[i].x = 0 # abs(new_force[i].x)
            if velocity[i].x < 0 :
                velocity[i].x  = 0 # abs(velocity[i].x)
            # velocity[i].x = 0
        if position[i].x > right_x:
            position[i].x  = right_x
            # new_force[i].x = 0 # -abs(new_force[i].x)
            if velocity[i].x > 0 :
                velocity[i].x  = 0 # -abs(velocity[i].x)
            # velocity[i].x = 0


@ti.func
def substep_jacobi():

    n = num_particles[None]
    dt2 = dt * dt

    # calc force
    for i in range(n) :
        new_force[i] = gravity * particle_mass[None]
        for j in range(n) :
            if rest_length[i,j] != 0 :
                new_force[i] += f_ij(i,j) 

    # collide_box()

    # init new velocity
    for i in range(n) :
        new_velocity[i] = velocity[i] + new_force[i] / particle_mass[None] * dt
        force[i] = new_force[i]

    mass = ti.Matrix([[particle_mass[None],0.0],[0.0,particle_mass[None]]])

    # fill A b
    for i in range(n) :
        for j in range(n) :
            A[i,j] *= 0.0

    for i in range(n) :
        for j in range(n) :
            if i==j :
                A[i,i] += mass
            elif rest_length[i, j] != 0:
                A[i,i] += dt2 * dfj_ij(i,j)
                A[i,j] -= dt2 * dfj_ij(i,j)

    for i in range(n) :
        b[i] = new_force[i] * dt + mass @ velocity[i]
        for j in range(n) :
            if i==j :
                b[i] += 0.0 # velocity[j]
            elif rest_length[i,j] != 0 :
                b[i] += 0.0 # dt2 * df_ij(i,j) @ velocity[j] 
                
    # solve equation for new velocity
    solve_equation()

    # Compute new position
    for i in range(n) :
        # if position[i].y > bottom_y :
        position[i] += (velocity[i] + new_velocity[i]) * ht
        # position[i] += new_velocity[i] * dt
        velocity[i] = new_velocity[i]
        velocity[i] *= ti.exp(-dt * damping[None]) # damping

@ti.func
def substep_jacobi_semi():
    n = num_particles[None]
    ht2 = ht * ht

    # calc force
    for i in range(n) :
        new_force[i] = gravity * particle_mass[None]
        for j in range(n) :
            if rest_length[i,j] != 0 :
                new_force[i] += f_ij(i,j) 

    # init new velocity
    for i in range(n) :
        new_velocity[i] = velocity[i] + new_force[i] / particle_mass[None] * dt
        force[i] = new_force[i]

    mass = ti.Matrix([[particle_mass[None],0.0],[0.0,particle_mass[None]]])

    # fill A b
    for i in range(n) :
        for j in range(n) :
            A[i,j] *= 0.0

    for i in range(n) :
        A[i,i] += mass
        for j in range(n) :
            if rest_length[i, j] != 0:
                A[i,i] += ht2 * dfj_ij(i,j)
                A[i,j] -= ht2 * dfj_ij(i,j)

    for i in range(n) :
        b[i] = new_force[i] * ht * 2 + mass @ velocity[i]
        for j in range(n) :
            if rest_length[i,j] != 0 :
                b[i] -= ht2 * dfj_ij(i,j) @ velocity[i] 
                b[i] += ht2 * dfj_ij(i,j) @ velocity[j] 
                
    # solve equation for new velocity
    solve_equation()

    # Compute new position
    for i in range(n) :
        position[i] += (velocity[i] + new_velocity[i]) * ht
        velocity[i] = new_velocity[i]
        # velocity[i] *= ti.exp(-dt * damping[None]) # damping

@ti.kernel
def step_jacobi():
    for no_loop in range(1) :
        for step in range(10):
            substep_jacobi_semi()
            # substep_jacobi()
        
@ti.kernel
def new_particle(pos_x: ti.f32, pos_y: ti.f32): # Taichi doesn't support using Matrices as kernel arguments yet
    new_particle_id = num_particles[None]
    position[new_particle_id] = [pos_x, pos_y]
    velocity[new_particle_id] = [0, 0]
    num_particles[None] += 1

@ti.kernel
def hit_particle(pos_x: ti.f32, pos_y: ti.f32): 
    n = num_particles[None]
    for i in range(n) :
        dist = (position[i] - ti.Vector([pos_x,pos_y])).norm()
        if dist < 0.05 : # and position[i].y > bottom_y :
            position[i] = ti.Vector([pos_x,pos_y])

@ti.kernel
def conn_particle(pos_i: ti.i32, pos_j: ti.i32) :
    dist = (position[pos_i] - position[pos_j]).norm()
    rest_length[pos_i, pos_j] = dist # 0.1
    rest_length[pos_j, pos_i] = dist # 0.1
    
gui = ti.GUI('Mass Spring System', res=(512, 512), background_color=0xdddddd)


def init() :
    n_xs = 6
    n_ys = 6
    for i in range(n_xs) :
        for j in range(n_ys) :
            new_particle(0.25 + i * 0.1, 0.2 + bottom_y + j * 0.1)
            if i > 0 :
                conn_particle(i*n_ys+j,(i-1)*n_ys+(j+0))
            if j > 0 :
                conn_particle(i*n_ys+j,(i-0)*n_ys+(j-1))
            if i > 0 and j > 0 :
                conn_particle(i*n_ys+j,(i-1)*n_ys+(j-1))
                conn_particle((i-0)*n_ys+(j-1),(i-1)*n_ys+(j-0))

init()
# new_particle(0.3, 0.2)
# new_particle(0.3, 0.5)
# new_particle(0.3, 0.5)
# new_particle(0.4, 0.4)

def debug_without_gui() :
    while True:
                    
        if not paused[None]:
            step_jacobi()
            print('resi out : ',resi_out[0],resi_out[1])
            print(f'residual={residual():0.10f}')
        
        X = position.to_numpy()
        
        for i in range(num_particles[None]):
            for j in range(i + 1, num_particles[None]):
                if rest_length[i, j] != 0:
                    print('4g5 :',math.sqrt((X[i][0]-X[j][0])**2 +(X[i][1]-X[j][1])**2)  )

        time.sleep(0.1)


# debug_without_gui()

while True:
    for e in gui.get_events(ti.GUI.PRESS):
        if e.key in [ti.GUI.ESCAPE, ti.GUI.EXIT]:
            exit()
        elif e.key == gui.SPACE:
            paused[None] = not paused[None]
        elif e.key == ti.GUI.LMB:
            # new_particle(e.pos[0], e.pos[1])
            hit_particle(e.pos[0], e.pos[1])
        elif e.key == 'c':
            num_particles[None] = 0
            rest_length.fill(0)
            init()
        elif e.key == 's':
            if gui.is_pressed('Shift'):
                spring_stiffness[None] /= 1.1
            else:
                spring_stiffness[None] *= 1.1
        elif e.key == 'd':
            if gui.is_pressed('Shift'):
                damping[None] /= 1.1
            else:
                damping[None] *= 1.1

                
    if not paused[None]:
        step_jacobi()
        print('resi out : ',resi_out[0],resi_out[1])
        print(f'residual={residual():0.10f}')
    
    X = position.to_numpy()
    gui.circles(X[:num_particles[None]], color=0xffaa77, radius=8)
    
    gui.line(begin=(0.0, bottom_y), end=(1.0, bottom_y), color=0x0, radius=1)
    
    for i in range(num_particles[None]):
        for j in range(i + 1, num_particles[None]):
            if rest_length[i, j] != 0:
                gui.line(begin=X[i], end=X[j], radius=2, color=0x445566)
    gui.text(content=f'C: clear all; Space: pause', pos=(0, 0.95), color=0x0)
    gui.text(content=f'S: Spring stiffness {spring_stiffness[None]:.1f}', pos=(0, 0.9), color=0x0)
    gui.text(content=f'D: damping {damping[None]:.2f}', pos=(0, 0.85), color=0x0)
    gui.show()

