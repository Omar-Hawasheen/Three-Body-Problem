import numpy as np
import pandas as pd 
import math 
from vpython import *
import matplotlib.pyplot as plt

#Calestial Bodys
class CelestialBodies:
    def __init__(self,r,v,m):
            self.r = r.copy()
            self.v = v.copy()
            self.m = m
            self.a = np.zeros(3)
            self.f = np.zeros(3)

    #Force
    def Force (self,obj1,obj2):
        eps = 1e-5
        #R
        rAB = (math.sqrt((obj1.r[0]-self.r[0])**2 + (obj1.r[1]-self.r[1])**2 + (obj1.r[2]-self.r[2])**2)) + eps
        rAC = (math.sqrt((obj2.r[0]-self.r[0])**2 + (obj2.r[1]-self.r[1])**2 + (obj2.r[2]-self.r[2])**2)) + eps
        #Force reset
        self.f[:] = 0.0
        #Force computational X
        self.f[0] += G*(self.m*obj1.m/math.pow(abs(rAB),3))*(obj1.r[0]-self.r[0])
        self.f[0] += G*(self.m*obj2.m/math.pow(abs(rAC),3))*(obj2.r[0]-self.r[0])
        #Force computational Y
        self.f[1] += G*(self.m*obj1.m/math.pow(abs(rAB),3))*(obj1.r[1]-self.r[1])
        self.f[1] += G*(self.m*obj2.m/math.pow(abs(rAC),3))*(obj2.r[1]-self.r[1])
        #Force computational Z
        self.f[2] += G*(self.m*obj1.m/math.pow(abs(rAB),3))*(obj1.r[2]-self.r[2])
        self.f[2] += G*(self.m*obj2.m/math.pow(abs(rAC),3))*(obj2.r[2]-self.r[2])

    def velocity_verlet(self, obj1, obj2):
        # 1) Compute force and acceleration at time t
        self.Force(obj1, obj2)
        a_old = self.f / self.m
        # 2) Update position
        self.r = self.r + self.v * time + 0.5 * a_old * time**2
        # 3) Recompute force and acceleration at time t + dt
        self.Force(obj1, obj2)
        a_new = self.f / self.m
        # 4) Update velocity
        self.v = self.v + 0.5 * (a_old + a_new) * time

def total_energy(b1, b2, b3):
    eps = 1e-5
    # Kinetic energy
    KE = 0.5*b1.m*np.dot(b1.v,b1.v) + \
         0.5*b2.m*np.dot(b2.v,b2.v) + \
         0.5*b3.m*np.dot(b3.v,b3.v)
    # Potential energy
    r12 = np.linalg.norm(b1.r - b2.r) + eps
    r13 = np.linalg.norm(b1.r - b3.r) + eps
    r23 = np.linalg.norm(b2.r - b3.r) + eps
    PE = -G*(b1.m*b2.m/r12) - G*(b1.m*b3.m/r13) - G*(b2.m*b3.m/r23)
    return KE + PE

energy_log = []

G = 1.0
time = 0.0008
'''
#Random test

b11 = CelestialBodies(
    r=np.array([-1.0, 0.0, 0.0]),
    v=np.array([0.0,  0.3, 0.0]),
    m=1.0
)

b22 = CelestialBodies(
    r=np.array([ 1.0, 0.0, 0.0]),
    v=np.array([0.0, -0.3, 0.0]),
    m=1.0
)

b33 = CelestialBodies(
    r=np.array([0.0, 0.5, 0.0]),
    v=np.array([0.0, 0.0, 0.0]),
    m=1.0
)

'''
'''

# Figure-8 (famous periodic solution!)

b11 = CelestialBodies(
    r=np.array([-0.97000436, 0.24308753, 0.0]),
    v=np.array([0.466203685, 0.43236573, 0.0]),
    m=1.0
)
b22 = CelestialBodies(
    r=np.array([0.0, 0.0, 0.0]),
    v=np.array([-0.93240737, -0.86473146, 0.0]),
    m=1.0
)
b33 = CelestialBodies(
    r=np.array([0.97000436, -0.24308753, 0.0]),
    v=np.array([0.466203685, 0.43236573, 0.0]),
    m=1.0
)


'''

b11 = CelestialBodies(
    r=np.array([1.0, 0.0, 0.0]),
    v=np.array([0.0, 0.5, 0.0]),
    m=1.0
)
b22 = CelestialBodies(
    r=np.array([-0.5, 0.866, 0.0]),
    v=np.array([-0.433, -0.25, 0.0]),
    m=1.0
)
b33 = CelestialBodies(
    r=np.array([-0.5, -0.866, 0.0]),
    v=np.array([0.433, -0.25, 0.0]),
    m=1.0
)



scene = canvas(title='Three Body Problem', width=1200, height=800,background=color.black)

# Create bodies
ball_1 = sphere(pos=vector(b11.r[0], b11.r[1], b11.r[2]),radius=0.05,color=color.red,make_trail=True,trail_radius=0.005,retain=2000)

ball_2 = sphere(pos=vector(b22.r[0], b22.r[1], b22.r[2]),radius=0.05,color=color.magenta,make_trail=True,trail_radius=0.005,retain=2000)

ball_3 = sphere(pos=vector(b33.r[0], b33.r[1], b33.r[2]),radius=0.05,color=color.green,make_trail=True,trail_radius=0.005,retain=2000)

# Center of mass marker
com = sphere(pos=vector(0, 0, 0),
             radius=0.02,
             color=color.yellow,
             opacity=0.3)

# Simulation loop
max_steps = 50000
for step in range(max_steps):
    rate(100)  # Increased to 100 for smoother animation
    
    # Phase 1: acceleration at t
    b11.Force(b22, b33); a1_old = b11.f / b11.m
    b22.Force(b11, b33); a2_old = b22.f / b22.m
    b33.Force(b11, b22); a3_old = b33.f / b33.m
    
    # Phase 2: position update
    b11.r += b11.v*time + 0.5*a1_old*time**2
    b22.r += b22.v*time + 0.5*a2_old*time**2
    b33.r += b33.v*time + 0.5*a3_old*time**2
    
    # Update sphere positions
    ball_1.pos = vector(b11.r[0], b11.r[1], b11.r[2])
    ball_2.pos = vector(b22.r[0], b22.r[1], b22.r[2])
    ball_3.pos = vector(b33.r[0], b33.r[1], b33.r[2])
    
    # Update center of mass
    total_mass = b11.m + b22.m + b33.m
    com_pos = (b11.m*b11.r + b22.m*b22.r + b33.m*b33.r) / total_mass
    com.pos = vector(com_pos[0], com_pos[1], com_pos[2])
    
    # Phase 3: acceleration at t + dt
    b11.Force(b22, b33); a1_new = b11.f / b11.m
    b22.Force(b11, b33); a2_new = b22.f / b22.m
    b33.Force(b11, b22); a3_new = b33.f / b33.m
    
    # Phase 4: velocity update
    b11.v += 0.5*(a1_old + a1_new)*time
    b22.v += 0.5*(a2_old + a2_new)*time
    b33.v += 0.5*(a3_old + a3_new)*time

    energy_log.append(total_energy(b11, b22, b33))

plt.figure(figsize=(10, 4))
plt.plot(energy_log, color='cyan', linewidth=0.8)
plt.title('Total Energy Over Time')
plt.xlabel('Step')
plt.ylabel('Energy')
plt.grid(True, alpha=0.3)
plt.tight_layout()
plt.savefig(r'C:\Users\ASUS\OneDrive\Desktop\Pprog\Python personal projects\Three Body Problem-Data Science\images\Energy1', dpi=150)
plt.show()
