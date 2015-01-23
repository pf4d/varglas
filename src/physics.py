r"""
This module contains the physics objects for the flowline ice model, does all 
of the finite element work, and Newton solves for non-linear equations.

**Classes**

:class:`~src.physics.AdjointVelocityBP` -- Linearized adjoint of 
Blatter-Pattyn, and functions for using it to calculate the gradient and 
objective function

:class:`~src.physics.Age` -- Solves the pure advection equation for ice 
age with a age zero Dirichlet boundary condition at the ice surface above the 
ELA. 
Stabilized with Streamline-upwind-Petrov-Galerking/GLS.

:class:`~src.physics.Enthalpy` -- Advection-Diffusion equation for ice sheets

:class:`~src.physics.FreeSurface` -- Calculates the change in surface 
elevation, and updates the mesh and surface function

:class:`~src.physics.SurfaceClimate` -- PDD and surface temperature model 
based on lapse rates

:class:`~src.physics.VelocityStokes` -- Stokes momentum balance

:class:`~src.physics.VelocityBP` -- Blatter-Pattyn momentum balance
"""

from pylab      import ndarray
from fenics     import *
from termcolor  import colored, cprint
from helper     import raiseNotDefined
from io         import print_text, print_min_max
import numpy as np
import numpy.linalg as linalg


class Physics(object):
  """
  This abstract class outlines the structure of a physics calculation.
  """
  def solve(self):
    """
    Solves the physics calculation.
    """
    raiseNotDefined()
  
  def color(self):
    """
    return the default color for this class.
    """
    return 'cyan'


class VelocityStokes(Physics):
  r"""  
  This class solves the non-linear Blatter-Pattyn momentum balance, 
  given a possibly non-uniform temperature field.
  
  :param model  : An instantiated 2D flowline ice :class:`~src.model.Model`
  :param config : Dictionary object containing information on physical 
	                attributes such as velocties, age, and surface climate

  **Equations**
 
  +-------------------+---------------+---------------------------------------+
  |Equation Name      |Condition      | Formula                               |
  +===================+===============+=======================================+
  |Variational        |               |.. math::                              |
  |Principle          |               |   \mathcal{A}[\textbf{u}, P] =        |
  |For Power Law      |               |   \int\limits_{\Omega}\frac{2n}{n+1}  |
  |Rheology           |               |   \eta\left(\dot{\epsilon}^{2}\right) |
  |                   |None           |   \dot{\epsilon}^{2} + \rho\textbf{g} |
  |                   |               |   \cdot\textbf{u}-P\nabla\cdot        |
  |                   |               |   \textbf{u}                          |
  |                   |               |   \ d\Omega+\int\limits_{\Gamma_{B}}  |
  |                   |               |   \frac{\beta^{2}}{2}H^{r}\textbf{u}  |
  |                   |               |   \cdot                               |
  |                   |               |   \textbf{u}+P\textbf{u}\cdot         |
  |                   |               |   \textbf{n} d\Gamma                  |
  +-------------------+---------------+---------------------------------------+
  |Rate of            |None           |.. math::                              |
  |strain             |               |   \eta\left(\dot{\epsilon}^{2}\right) |
  |                   |               |   =                                   |
  |tensor             |               |   b(T)\left[\dot{\epsilon}^{2}\right] |
  |                   |               |   ^{\frac{1-n}{2n}}                   |
  +-------------------+---------------+---------------------------------------+
  |Temperature        |Viscosity mode +.. math::                              |
  |Dependent          |is isothermal  |   b(T) = A_0^{\frac{-1}{n}}           |
  |Rate Factor        +---------------+---------------------------------------+
  |                   |Viscosity mode |Model dependent                        |
  |                   |is linear      |                                       |
  |                   +---------------+---------------------------------------+
  |                   |Viscosity mode |                                       |
  |                   |is full        |.. math::                              |
  |                   |               |   b(T) = \left[Ea(T)e^{-\frac{Q(T)}   |
  |                   |               |   {RT^*}}                             |
  |                   |               |   \right]^{\frac{-1}{n}}              |
  +-------------------+---------------+---------------------------------------+
  
  **Terms**

  +------------+-------------------------+------------------------------------+
  |Equation    |Term                     | Description                        |
  +============+=========================+====================================+
  |Variational |.. math::                |*Viscous dissipation* including     |
  |Principle   |                         |terms for strain rate dependent ice |
  |For Power   |   \frac{2n}{n+1}        |viscosity and the strain rate       |
  | Law        |   \eta\left(\dot        |tensor, respectively                |
  |Rheology    |   {\epsilon}^{2}        |                                    |
  |            |   \right)\dot{          |                                    |
  |            |   \epsilon}^{2}         |                                    |
  |            +-------------------------+------------------------------------+
  |            |.. math::                |*Graviataional potential* energy    |
  |            |   \rho\textbf{g}\cdot   |calculated using the density,       |
  |            |   \textbf{u}            |graviational force, and ice velocity|
  |            +-------------------------+------------------------------------+
  |            |.. math::                |*Incompressibility constraint*      |
  |            |    P\nabla\cdot         |included terms for pressure and the |
  |            |    \textbf{u}\ d\Omega  |divergence of the ice velocity      |
  |            +-------------------------+------------------------------------+
  |            |.. math::                |*Frictional head dissipation*       |
  |            |   \frac{\beta^{2}}      |including terms for the basal       |
  |            |   {2}H^{r}\textbf{u}    |sliding coefficient, ice thickness, |
  |            |   \cdot\textbf{u}       |and the ice velocity dotted into    |
  |            |                         |itself                              |
  |            +-------------------------+------------------------------------+
  |            |.. math::                |*Impenetrability constraint*        |
  |            |   P\textbf{u}\cdot      |calculated using the pressure and   |
  |            |   \textbf{n}            |the ice velocity dotted into the    |
  |            |                         |outward normal vector               |
  +------------+-------------------------+------------------------------------+
  |Rate of     |.. math::                |Temperature dependent rate factor,  |
  |strain      |   b(T)\left[\dot        |square of the second invarient of   |
  |tensor      |   {\epsilon}^{2}        |the strain rate tensor              |
  |            |   \right]^              |                                    |
  |            |  {\frac{1-n}{2n}}       |                                    |
  +------------+-------------------------+------------------------------------+
  |Temperature |.. math::                |Enhancement factor                  |
  |Dependent   |   E                     |                                    |
  |Rate Factor +-------------------------+------------------------------------+
  |            |.. math::                |Temperature dependent parameters    |
  |            |   a(T)                  |                                    |
  |            |                         |                                    |
  |            |   Q(T)                  |                                    |
  |            +-------------------------+------------------------------------+
  |            |.. math::                |Rate constant                       |
  |            |   R                     |                                    |
  +            +-------------------------+------------------------------------+
  |            |.. math::                |Temperature corrected for melting   |
  |            |   T^*                   |point dependence                    |
  +------------+-------------------------+------------------------------------+
  
  """
  def __init__(self, model, config):
    """ 
    Here we set up the problem, and do all of the differentiation and
    memory allocation type stuff.
    """
    s = "::: INITIALIZING STOKES VELOCITY PHYSICS :::"
    print_text(s, self.color())

    self.model    = model
    self.config   = config

    mesh          = model.mesh
    r             = config['velocity']['r']
    V             = model.V
    Q             = model.Q
    Q4            = model.Q4
    n             = model.n
    b             = model.b
    b_shf         = model.b_shf
    b_gnd         = model.b_gnd
    eta_shf       = model.eta_shf
    eta_gnd       = model.eta_gnd
    T             = model.T
    gamma         = model.gamma
    S             = model.S
    B             = model.B
    H             = S - B
    x             = model.x
    E             = model.E
    W             = model.W
    R             = model.R
    epsdot        = model.epsdot
    eps_reg       = model.eps_reg
    rhoi          = model.rhoi
    rhow          = model.rhow
    g             = model.g
    Pe            = model.Pe
    Sl_gnd        = model.Sl_gnd
    Sl_shf        = model.Sl_shf
    Pc            = model.Pc
    Nc            = model.Nc
    Pb            = model.Pb
    Lsq           = model.Lsq
    beta          = model.beta

    gradS         = model.gradS
    gradB         = model.gradB

    newton_params = config['velocity']['newton_params']
    
    # initialize the temperature depending on input type :
    if config['velocity']['use_T0']:
      model.assign_variable(T, config['velocity']['T0'])

    # initialize the bed friction coefficient :
    if config['velocity']['use_beta0']:
      model.assign_variable(beta, config['velocity']['beta0'])
    if config['velocity']['init_beta_from_U_ob']:
      U_ob = config['velocity']['U_ob']
      model.init_beta(beta, U_ob, r, gradS)
   
    # initialize the enhancement factor :
    model.assign_variable(E, config['velocity']['E'])
    
    # pressure boundary :
    class Depth(Expression):
      def eval(self, values, x):
        values[0] = min(0, x[2])
    D = Depth(element=Q.ufl_element())
    N = FacetNormal(mesh)
    
    # Check if there are non-linear solver parameters defined.  If not, set 
    # them to dolfin's default.  The default is not likely to converge if 
    # thermomechanical coupling is used.
    if newton_params:
      self.newton_params = newton_params
    
    else:
      self.newton_params = NonlinearVariationalSolver.default_parameters()
    
    # Define a test function
    Phi                  = TestFunction(Q4)

    # Define a trial function
    dU                   = TrialFunction(Q4)
    model.U              = Function(Q4)
    U                    = model.U
 
    phi, psi, xsi, kappa = Phi
    du,  dv,  dw,  dP    = dU
    u,   v,   w,   P     = U

    dx       = model.dx
    dx_s     = dx(1)
    dx_g     = dx(0)
    if model.mask != None:
      dx     = dx(1) + dx(0) # entire internal
    ds       = model.ds  
    dGnd     = ds(3)         # grounded bed
    dFlt     = ds(5)         # floating bed
    dSde     = ds(4)         # sides
    dBed     = dGnd + dFlt   # bed
    
    # initialize velocity to a previous solution :
    if config['velocity']['use_U0']:
      u0_f = Function(Q)
      v0_f = Function(Q)
      w0_f = Function(Q)
      P0_f = Function(Q)
      model.assign_variable(u0_f, config['velocity']['u0'])
      model.assign_variable(v0_f, config['velocity']['v0'])
      model.assign_variable(w0_f, config['velocity']['w0'])
      model.assign_variable(P0_f, config['velocity']['P0'])
      U0   = as_vector([u0_f, v0_f, w0_f, P0_f])
      model.assign_variable(U, project(U0, Q4))

    # Set the value of b, the temperature dependent ice hardness parameter,
    # using the most recently calculated temperature field, if expected.
    if   config['velocity']['viscosity_mode'] == 'isothermal':
      A0    = config['velocity']['A0']
      b     = A0**(-1/n)
      b_gnd = b
      b_shf = b
    
    elif config['velocity']['viscosity_mode'] == 'linear':
      b_shf = config['velocity']['eta_shf']
      b_gnd = config['velocity']['eta_gnd']
      n     = 1.0
    
    elif config['velocity']['viscosity_mode'] == 'b_control':
      b_shf = Function(Q)
      b_gnd = Function(Q)
      model.assign_variable(b_shf, config['velocity']['b_shf'])
      model.assign_variable(b_gnd, config['velocity']['b_gnd'])
    
    elif config['velocity']['viscosity_mode'] == 'constant_b':
      b     = config['velocity']['b']
      b_shf = b
      b_gnd = b
    
    elif config['velocity']['viscosity_mode'] == 'full':
      # Define ice hardness parameterization :
      a_T   = conditional( lt(T, 263.15), 1.1384496e-5, 5.45e10)
      Q_T   = conditional( lt(T, 263.15), 6e4,          13.9e4)
      b     = ( E *(a_T*(1 + 181.25*W))*exp(-Q_T/(R*T)) )**(-1/n)
      b_gnd = b
      b_shf = b
    
    elif config['velocity']['viscosity_mode'] == 'E_control':
      # Define ice hardness parameterization :
      a_T   = conditional( lt(T, 263.15), 1.1384496e-5, 5.45e10)
      Q_T   = conditional( lt(T, 263.15), 6e4,          13.9e4)
      E_shf = config['velocity']['E_shf'] 
      E_gnd = config['velocity']['E_gnd']
      b_shf = ( E_shf*(a_T*(1 + 181.25*W))*exp(-Q_T/(R*T)) )**(-1/n)
      b_gnd = ( E_gnd*(a_T*(1 + 181.25*W))*exp(-Q_T/(R*T)) )**(-1/n)
      model.E_shf = E_shf
      model.E_gnd = E_gnd
    
    else:
      print "Acceptable choices for 'viscosity_mode' are 'linear', " + \
            "'isothermal', or 'full'."

    # Second invariant of the strain rate tensor squared
    term    = + 0.5 * (+ 0.5*(   (u.dx(1) + v.dx(0))**2  \
                               + (u.dx(2) + w.dx(0))**2  \
                               + (v.dx(2) + w.dx(1))**2) \
                       + u.dx(0)**2 + v.dx(1)**2 + w.dx(2)**2) 
    epsdot  = term + eps_reg
    eta_shf = b_shf * epsdot**((1-n)/(2*n))
    eta_gnd = b_gnd * epsdot**((1-n)/(2*n))
    
    # 1) Viscous dissipation
    Vd_shf   = (2*n)/(n+1) * b_shf * epsdot**((n+1)/(2*n))
    Vd_gnd   = (2*n)/(n+1) * b_gnd * epsdot**((n+1)/(2*n))

    # 2) Potential energy
    Pe     = rhoi * g * w

    # 3) Dissipation by sliding
    Sl_gnd = 0.5 * beta**2 * H**r * (u**2 + v**2 + w**2)
    Sl_shf = 0.5 * Constant(1e-10) * (u**2 + v**2 + w**2)

    # 4) Incompressibility constraint
    Pc     = -P * (u.dx(0) + v.dx(1) + w.dx(2)) 
    
    # 5) Impenetrability constraint
    Nc     = P * (u*N[0] + v*N[1] + w*N[2])

    # 6) pressure boundary
    Pb     = - (rhoi*g*(S - x[2]) + rhow*g*D) * (u*N[0] + v*N[1] + w*N[2]) 

    g      = Constant((0.0, 0.0, g))
    h      = CellSize(mesh)
    tau    = h**2 / (12 * b * rhoi**2)
    Lsq    = -tau * dot( (grad(P) + rhoi*g), (grad(P) + rhoi*g) )
    
    # Variational principle
    A      = + Vd_shf*dx_s + Vd_gnd*dx_g + (Pe + Pc + Lsq)*dx \
             + Sl*dGnd + Nc*dBed
    if (not config['periodic_boundary_conditions']
        and config['use_pressure_boundary']):
      A += Pb*dSde

    model.A       = A
    model.epsdot  = epsdot
    model.eta_shf = eta_shf
    model.eta_gnd = eta_gnd
    model.b_shf   = b_shf
    model.b_gnd   = b_gnd
    model.Vd_shf  = Vd_shf
    model.Vd_gnd  = Vd_gnd
    model.Pe      = Pe
    model.Sl_gnd  = Sl_gnd
    model.Sl_shf  = Sl_shf
    model.Pc      = Pc
    model.Nc      = Nc
    model.Pb      = Pb
    model.Lsq     = Lsq
    model.u       = u
    model.v       = v
    model.w       = w
    model.P       = P

    # Calculate the first variation (the action) of the variational 
    # principle in the direction of the test function
    self.F = derivative(A, U, Phi)   

    # Calculate the first variation of the action (the Jacobian) in
    # the direction of a small perturbation in U
    self.J = derivative(self.F, U, dU)

  def solve(self):
    """ 
    Perform the Newton solve of the first order equations 
    """
    model  = self.model
    config = self.config
    Q4     = model.Q4
    Q      = model.Q

    self.bcs = []

    if config['velocity']['boundaries'] == 'homogeneous':
      self.bcs.append(DirichletBC(Q4.sub(0), 0.0, model.ff, 4))
      self.bcs.append(DirichletBC(Q4.sub(1), 0.0, model.ff, 4))
      self.bcs.append(DirichletBC(Q4.sub(2), 0.0, model.ff, 4))
      
    if config['velocity']['boundaries'] == 'solution':
      self.bcs.append(DirichletBC(Q4.sub(0), model.u, model.ff, 4))
      self.bcs.append(DirichletBC(Q4.sub(1), model.v, model.ff, 4))
      self.bcs.append(DirichletBC(Q4.sub(2), model.w, model.ff, 4))
      
    if config['velocity']['boundaries'] == 'user_defined':
      u_t = config['velocity']['u_lat_boundary']
      v_t = config['velocity']['v_lat_boundary']
      self.bcs.append(DirichletBC(Q4.sub(0), u_t, model.ff, 4))
      self.bcs.append(DirichletBC(Q4.sub(1), v_t, model.ff, 4))
      self.bcs.append(DirichletBC(Q4.sub(2), 0.0, model.ff, 4))
       
    # Solve the nonlinear equations via Newton's method
    s    = "::: solving full-Stokes velocity :::"
    print_text(s, self.color())
    solve(self.F == 0, model.U, bcs=self.bcs, J = self.J, 
          solver_parameters = self.newton_params)
    model.u, model.v, model.w, model.P = model.U.split(True)
    
    print_min_max(model.u, 'u')
    print_min_max(model.v, 'v')
    print_min_max(model.w, 'w')
    print_min_max(model.P, 'P')


class VelocityBP(Physics):
  r"""				
  This class solves the non-linear Blatter-Pattyn momentum balance, 
  given a possibly non-uniform temperature field.
  
  :param model  : An instantiated 2D flowline ice :class:`~src.model.Model`
  :param config : Dictionary object containing information on physical 
	                attributes such as velocties, age, and surface climate
  
  This class uses a simplification of the full Stokes' functional by expressing
  vertical velocities in terms of horizontal ones through incompressibility
  and bed impenetrability constraints.
  	
  **Equations**
	
  +-------------------+---------------+---------------------------------------+
  |Equation           |Condition      | Formula                               |
  +===================+===============+=======================================+
  |Variational        |               |.. math::                              |
  |Principle          |               |   \mathcal{A}\left[\textbf{u}_{| |}   |
  |                   |               |   \right]=                            |
  |For Power          |               |   \int\limits_{\Omega}\frac{2n}{n+1}  |
  |Law                |               |   \eta\left(\dot{\epsilon}^2_1\right) |
  |Rheology           |None           |   \dot{\epsilon}^2_1 + \rho g         |
  |                   |               |   \textbf{u}_{| |}\cdot\nabla_{| |}S  |
  |                   |               |   \ d\Omega+\int\limits_{\Gamma_{B}}  |
  |                   |               |   \frac{\beta^{2}}{2}H^{r}\textbf     |
  |                   |               |   {u}_{| |}                           |
  |                   |               |   \cdot\textbf{u}_{| |}               |
  |                   |               |   \ d\Gamma                           |
  +-------------------+---------------+---------------------------------------+
  |Rate of            |None           |.. math::                              |
  |strain             |               |   \eta\left(\dot{\epsilon}^{2}\right)=|
  |tensor             |               |   b(T)\left[\dot{\epsilon}^{2}\right] |
  |                   |               |   ^{\frac{1-n}{2n}}                   |
  +-------------------+---------------+---------------------------------------+
  |Temperature        |Viscosity mode +.. math::                              |
  |Dependent          |is isothermal  |   b(T) = A_0^{\frac{-1}{n}}           |
  |Rate Factor        +---------------+---------------------------------------+
  |                   |Viscosity mode |Model dependent                        |
  |                   |is linear      |                                       |
  +                   +---------------+---------------------------------------+
  |                   |Viscosity mode |                                       |
  |                   |is full        |.. math::                              |
  |                   |               |   b(T) = \left[Ea(T)e^{-\frac{Q(T)    |
  |                   |               |   }{RT^*}}                            |
  |                   |               |   \right]^{\frac{-1}{n}}              |
  +-------------------+---------------+---------------------------------------+
  |Incompressibility  |.. math::      |.. math::                              |
  |                   |   w_b=\textbf |   w\left(u\right)=-\int\limits^{z}_{B}|
  |                   |   {u}_{| | b} |   \nabla_{| |}\textbf{u}_{| |}dz'     |
  |                   |   \cdot       |                                       |
  |                   |   \nabla_{| | |                                       |
  |                   |   }B          |                                       |
  +-------------------+---------------+---------------------------------------+
  
  **Terms**

  +-------------------+-------------------------------+-----------------------+
  |Equation Name      |Term                           | Description           |
  +===================+===============================+=======================+
  |Variational        |.. math::                      |*Viscous dissipation*  |
  |Principle          |                               |including              |
  |For Power Law      |                               |terms for strain rate  |
  |Rheology           |                               |dependent ice          |
  |                   |   \frac{2n}{n+1}\eta\left(    |viscosity and the      |
  |                   |   \dot{\epsilon}^2_1\right)   |strain rate            |
  |                   |   \dot{\epsilon}^2_1          |tensor, respectively   |
  |                   |                               |                       |
  |                   +-------------------------------+-----------------------+
  |                   |.. math::                      |*Graviataional         |
  |                   |   \rho g                      |potential* energy      |
  |                   |   \textbf{u}\cdot\nabla       |calculated using the   |
  |                   |   _{| |}S                     |density,               |
  |                   |                               |graviational force,    |
  |                   |                               |and horizontal         |
  |                   |                               |ice velocity dotted    |
  |                   |                               |into the               |
  |                   |                               |gradient of the        |
  |                   |                               |surface elevation of   |
  |                   |                               |the ice                |
  |                   +-------------------------------+-----------------------+
  |                   |.. math::                      |*Frictional head       |
  |                   |                               |dissipation*           |
  |                   |   \frac{\beta^{2}}{2}H^{r}    |including terms for    |
  |                   |   \textbf{u}_{| |}\cdot       |the basal              |
  |                   |   \textbf{u}_{| |}            |sliding coefficient,   |
  |                   |                               |ice thickness,         |
  |                   |                               |and the horizontal     |
  |                   |                               |ice velocity           |
  |                   |                               |dotted into itself     |
  +-------------------+-------------------------------+-----------------------+
  |Rate of            |.. math::                      |Temperature dependent  |
  |strain             |                               |rate factor,           |
  |tensor             |   b(T)\left[\dot{\epsilon}    |square of the second   |
  |                   |   ^{2}\right]                 |invarient of           |
  |                   |   ^{\frac{1-n}{2n}}           |the strain rate        |
  |                   |                               |tensor                 |
  |                   |                               |                       |
  +-------------------+-------------------------------+-----------------------+
  |Temperature        |.. math::                      |Enhancement factor     |
  |Dependent          |   E                           |                       |
  |Rate Factor        +-------------------------------+-----------------------+
  |                   |.. math::                      |Temperature            |
  |                   |                               |dependent parameters   |
  |                   |   a(T)                        |                       |
  |                   |   Q(T)                        |                       |
  |                   |                               |                       |
  |                   +-------------------------------+-----------------------+
  |                   |.. math::                      |Rate constant          |
  |                   |   R                           |                       |
  +                   +-------------------------------+-----------------------+
  |                   |.. math::                      |Temperature corrected  |
  |                   |                               |for melting            |
  |                   |   T^*                         |point dependence       |
  +-------------------+-------------------------------+-----------------------+
  """
  def __init__(self, model, config):
    """ 
    Here we set up the problem, and do all of the differentiation and
    memory allocation type stuff.
    """
    s = "::: INITIALIZING BP VELOCITY PHYSICS :::"
    print_text(s, self.color())

    self.model    = model
    self.config   = config
    
    mesh          = model.mesh
    r             = config['velocity']['r']
    V             = model.V
    Q             = model.Q
    Q2            = model.Q2
    n             = model.n
    b_shf         = model.b_shf
    b_gnd         = model.b_gnd
    eta_shf       = model.eta_shf
    eta_gnd       = model.eta_gnd
    T             = model.T
    T_w           = model.T_w
    gamma         = model.gamma
    S             = model.S
    B             = model.B
    H             = S - B
    x             = model.x
    E             = model.E
    E_gnd         = model.E_gnd
    E_shf         = model.E_shf
    W             = model.W_r
    R             = model.R
    epsdot        = model.epsdot
    eps_reg       = model.eps_reg
    rhoi          = model.rhoi
    rhow          = model.rhow
    g             = model.g
    Pe            = model.Pe
    Sl_shf        = model.Sl_shf
    Sl_gnd        = model.Sl_gnd
    Pb            = model.Pb
    beta          = model.beta
    w             = model.w
    
    gradS         = model.gradS
    gradB         = model.gradB

    # pressure boundary :
    class Depth(Expression):
      def eval(self, values, x):
        values[0] = min(0, x[2])
    D = Depth(element=Q.ufl_element())
    N = FacetNormal(mesh)
    
    # Define a test function
    Phi      = TestFunction(Q2)

    # Define a trial function
    dU       = TrialFunction(Q2)
    model.U  = Function(Q2)
    U        = model.U 

    phi, psi = Phi
    du,  dv  = dU
    u,   v   = U

    # vertical velocity components :
    chi      = TestFunction(Q)
    dw       = TrialFunction(Q)

    dx       = model.dx
    dx_s     = dx(1)
    dx_g     = dx(0)
    if model.mask != None:
      dx     = dx(1) + dx(0) # entire internal
    ds       = model.ds  
    dGnd     = ds(3)         # grounded bed
    dFlt     = ds(5)         # floating bed
    dSde     = ds(4)         # sides
    dBed     = dGnd + dFlt   # bed
    
    # initialize velocity to a previous solution :
    if config['velocity']['use_U0']:
      s = "::: using initial velocity :::"
      print_text(s, self.color())
      model.assign_variable(U, project(as_vector([model.u, model.v]), Q2))
      model.assign_variable(w, model.w)
      print_min_max(model.u, 'u')
      print_min_max(model.v, 'v')
      print_min_max(model.w, 'w')

    # Set the value of b, the temperature dependent ice hardness parameter,
    # using the most recently calculated temperature field, if expected.
    if   config['velocity']['viscosity_mode'] == 'isothermal':
      A = config['velocity']['A']
      s = "::: using isothermal visosity formulation :::"
      print_text(s, self.color())
      print_min_max(A, 'A')
      b     = A**(-1/n)
      b_gnd = b
      b_shf = b
    
    elif config['velocity']['viscosity_mode'] == 'linear':
      b_gnd = config['velocity']['eta_gnd']
      b_shf = config['velocity']['eta_shf']
      s     = "::: using linear visosity formulation :::"
      print_text(s, self.color())
      print_min_max(eta_shf, 'eta_shf')
      print_min_max(eta_gnd, 'eta_gnd')
      n     = 1.0
    
    elif config['velocity']['viscosity_mode'] == 'b_control':
      b_shf   = config['velocity']['b_shf']
      b_gnd   = config['velocity']['b_gnd']
      s       = "::: using b_control visosity formulation :::"
      print_text(s, self.color())
      print_min_max(b_shf, 'b_shf')
      print_min_max(b_gnd, 'b_gnd')
    
    elif config['velocity']['viscosity_mode'] == 'constant_b':
      b     = config['velocity']['b']
      b_shf = b
      b_gnd = b
      s = "::: using constant_b visosity formulation :::"
      print_text(s, self.color())
      print_min_max(b, 'b')
    
    elif config['velocity']['viscosity_mode'] == 'full':
      s     = "::: using full visosity formulation :::"
      print_text(s, self.color())
      a_T   = conditional( lt(T, 263.15), 1.1384496e-5, 5.45e10)
      Q_T   = conditional( lt(T, 263.15), 6e4,          13.9e4)
      b     = ( E*(a_T*(1 + 181.25*W))*exp(-Q_T/(R*T)) )**(-1/n)
      b_gnd = b
      b_shf = b
    
    elif config['velocity']['viscosity_mode'] == 'E_control':
      E_shf = config['velocity']['E_shf'] 
      E_gnd = config['velocity']['E_gnd']
      s     = "::: using E_control visosity formulation :::"
      print_text(s, self.color())
      print_min_max(E_shf, 'E_shf')
      print_min_max(E_gnd, 'E_gnd')
      a_T   = conditional( lt(T, 263.15), 1.1384496e-5, 5.45e10)
      Q_T   = conditional( lt(T, 263.15), 6e4,          13.9e4)
      b_shf = ( E_shf*(a_T*(1 + 181.25*W))*exp(-Q_T/(R*T)) )**(-1/n)
      b_gnd = ( E_gnd*(a_T*(1 + 181.25*W))*exp(-Q_T/(R*T)) )**(-1/n)
      model.E_shf = E_shf
      model.E_gnd = E_gnd
    
    else:
      print "Acceptable choices for 'viscosity_mode' are 'linear', " + \
            "'isothermal', 'b_control', 'constant_b', 'E_control', or 'full'."

    # initialize the bed friction coefficient :
    if config['velocity']['init_beta_from_stats']:
      s    = "::: initializing beta from stats :::"
      print_text(s, self.color())
      U_ob  = config['velocity']['U_ob']
      q_geo = config['enthalpy']['q_geo']
      T_s   = config['enthalpy']['T_surface']
      adot  = model.adot
      Mb    = model.Mb
      Ubar  = model.Ubar

      adot_v = adot.vector().array()
      adot_v[adot_v < 0] = 0
      model.assign_variable(adot, adot_v)

      Ubar_v = Ubar.vector().array()
      Ubar_v[Ubar_v < 0] = 0
      model.assign_variable(Ubar, Ubar_v)

      absB  = Function(Q)
      B_v   = B.vector().array()
      B_v   = np.abs(B_v)
      model.assign_variable(absB, B_v)

      nS   = Function(Q)
      gSx  = project(S.dx(0)).vector().array()
      gSy  = project(S.dx(1)).vector().array()
      nS_v = np.sqrt(gSx**2 + gSy**2 + 1e-16)
      model.assign_variable(nS, nS_v)

      nB   = Function(Q)
      gBx  = project(B.dx(0)).vector().array()
      gBy  = project(B.dx(1)).vector().array()
      nB_v = np.sqrt(gBx**2 + gBy**2 + 1e-16)
      model.assign_variable(nB, nB_v)

      U_v  = as_vector([u,v,w])

      x0   = Mb
      x1   = S
      x2   = T
      x3   = T_s
      x4   = nS
      x5   = absB
      x6   = nB
      x7   = H
      x11  = ln(sqrt(inner(U_v,U_v)) + 1)
      x12  = ln(Ubar + 1)
      x13  = q_geo
      x14  = adot

      X    = [x0,x1,x2,x3,x4,x5,x6,x7,x11,x12,x14]
      X_i  = []
      X_i.extend(X)

      for i,xx in enumerate(X):
        print_min_max(xx, 'x' + str(i))
     
      bhat = [-1.77918639e+01,   2.20011439e-01,   1.32393565e-03,
               8.59953797e-02,   7.07178347e-02,  -3.52604627e+01,
              -5.05518896e-04,   3.67367905e+01,   1.59990657e-04,
               5.03857943e-01,  -7.54116576e-02,  -1.29721750e+00,
               1.61983883e-06,  -1.24991572e-03,   5.28870555e-04,
              -4.80464581e-02,  -1.12625188e-05,   2.52433468e-02,
               2.01589616e-05,  -8.87642978e-03,   6.02403721e-04,
              -3.59061473e-03,   2.56542080e-06,  -7.44499027e-06,
               1.04027323e-04,   7.02704009e-08,   3.33015434e-04,
              -5.46882463e-08,  -8.42303711e-05,   3.95678410e-05,
               1.81373572e-04,  -2.47195299e-04,   6.64150974e-02,
               7.48808497e-06,  -8.13631763e-02,  -9.27882915e-06,
              -2.50951441e-03,  -1.17571686e-03,   3.27624352e-02,
               8.08053928e-02,  -6.54939178e-06,  -5.77306748e-02,
               9.26525905e-06,  -8.98656304e-04,   9.11970998e-04,
              -2.90602485e-02,  -4.23124413e-05,  -2.46680922e+00,
               1.18344308e-03,   1.91908261e-01,  -3.61928613e-01,
              -2.69345178e+00,  -5.91708505e-04,   1.55306896e-09,
               6.39983323e-05,  -5.56000393e-05,   3.72891879e-05,
              -1.70122837e-04,  -1.99614266e-01,   1.74721495e-01,
               1.38037415e+00,   4.92579356e-06,   2.98879185e-05,
              -1.81560116e-04,   4.09910395e-02,   6.07800637e-02,
              -2.00998100e-02] 
      
      for i,xx in enumerate(X):
        for yy in X[i+1:]:
          X_i.append(xx*yy)
      
      beta_f = Constant(bhat[0])
      
      for xx,bb in zip(X_i, bhat[1:]):
        beta_f += Constant(bb)*xx
      beta_f      = exp(beta_f) - Constant(100.0)
      beta        = project(beta_f, Q)
      beta_v      = beta.vector().array()
      beta_v[beta_v < 0.0] = 0.0
      model.assign_variable(beta, beta_v)
      print_min_max(beta, 'beta0')
      self.beta_f = beta_f

    # initialize the enhancement factor :
    model.assign_variable(E, config['velocity']['E'])
    
    # second invariant of the strain rate tensor squared :
    term    = 0.5 * (0.5 * (u.dx(2)**2 + v.dx(2)**2 + (u.dx(1) + v.dx(0))**2) \
                     + u.dx(0)**2 + v.dx(1)**2 + (u.dx(0) + v.dx(1))**2 )
    epsdot  =  term + eps_reg
    eta_shf =  b_shf * epsdot**((1-n)/(2*n))
    eta_gnd =  b_gnd * epsdot**((1-n)/(2*n))

    # 1) Viscous dissipation
    Vd_shf   = (2*n)/(n+1) * b_shf * epsdot**((n+1)/(2*n))
    Vd_gnd   = (2*n)/(n+1) * b_gnd * epsdot**((n+1)/(2*n))

    # 2) Potential energy
    Pe       = rhoi * g * (u*gradS[0] + v*gradS[1])

    # 3) Dissipation by sliding
    Sl_shf   = 0.5 * Constant(1e-10) * (u**2 + v**2)
    Sl_gnd   = 0.5 * beta**2 * H**r * (u**2 + v**2)
    
    # 4) pressure boundary
    Pb       = - (rhoi*g*(S - x[2]) + rhow*g*D) * (u*N[0] + v*N[1]) 

    # Variational principle
    A        = Vd_shf*dx_s + Vd_gnd*dx_g + Pe*dx + Sl_gnd*dGnd + Sl_shf*dFlt
    if (not config['periodic_boundary_conditions']
        and config['use_pressure_boundary']):
      A += Pb*dSde

    # Calculate the first variation of the action 
    # in the direction of the test function
    self.F   = derivative(A, U, Phi)

    # Calculate the first variation of the action (the Jacobian) in
    # the direction of a small perturbation in U
    self.J   = derivative(self.F, U, dU)
   
    self.w_R = (u.dx(0) + v.dx(1) + dw.dx(2))*chi*dx - \
               (u*N[0] + v*N[1] + dw*N[2])*chi*dBed
    
    # Set up linear solve for vertical velocity.
    self.aw = lhs(self.w_R)
    self.Lw = rhs(self.w_R)
    
    # list of boundary conditions
    self.bcs  = []
    self.bc_w = None
      
    # add lateral boundary conditions :  
    if config['velocity']['boundaries'] == 'solution':
      self.bcs.append(DirichletBC(Q2.sub(0), model.u, model.ff, 4))
      self.bcs.append(DirichletBC(Q2.sub(1), model.v, model.ff, 4))
      self.bc_w = DirichletBC(Q, model.w, model.ff, 4)
      
    elif config['velocity']['boundaries'] == 'homogeneous':
      self.bcs.append(DirichletBC(Q2.sub(0), 0.0, model.ff, 4))
      self.bcs.append(DirichletBC(Q2.sub(1), 0.0, model.ff, 4))
      self.bc_w = DirichletBC(Q, 0.0, model.ff, 4)
    
    elif config['velocity']['boundaries'] == 'user_defined':
      u_t = config['velocity']['u_lat_boundary']
      v_t = config['velocity']['v_lat_boundary']
      w_t = config['velocity']['w_lat_boundary']
      self.bcs.append(DirichletBC(Q2.sub(0), u_t, model.ff, 4))
      self.bcs.append(DirichletBC(Q2.sub(1), v_t, model.ff, 4))
      self.bc_w = DirichletBC(Q, w_t, model.ff, 4)

    model.eta_shf = eta_shf
    model.eta_gnd = eta_gnd
    model.b_shf   = b_shf
    model.b_gnd   = b_gnd
    model.Vd_shf  = Vd_shf
    model.Vd_gnd  = Vd_gnd
    model.Sl_shf  = Sl_shf
    model.Sl_gnd  = Sl_gnd
    model.Pe      = Pe
    model.Pb      = Pb
    model.A       = A
    model.T       = T
    model.beta    = beta
    model.E       = E

  def solve(self):
    """ 
    Perform the Newton solve of the first order equations 
    """
    model  = self.model
    config = self.config
    
    # solve nonlinear system :
    s    = "::: solving BP horizontal velocity :::"
    print_text(s, self.color())
    solve(self.F == 0, model.U, J = self.J, bcs = self.bcs,
          solver_parameters = config['velocity']['newton_params'])
    model.u,model.v = model.U.split(True)
    print_min_max(model.u, 'u')
    print_min_max(model.v, 'v')
    
    # solve for vertical velocity :
    s    = "::: solving BP vertical velocity :::"
    print_text(s, self.color())
    sm = config['velocity']['vert_solve_method']
    solve(self.aw == self.Lw, model.w, bcs = self.bc_w,
          solver_parameters = {"linear_solver" : sm})
    print_min_max(model.w, 'w')
    
    if config['velocity']['init_beta_from_stats']:
      s    = "::: updating statistical beta :::"
      print_text(s, self.color())
      beta = project(self.beta_f, model.Q)
      beta_v = beta.vector().array()
      beta_v[beta_v < 0.0] = 0.0
      model.assign_variable(model.beta, beta_v)
      print_min_max(model.beta, 'beta')
    
    # solve for pressure :
    s    = "::: solving BP pressure :::"
    print_text(s, self.color())
    model.calc_pressure()
    print_min_max(model.P, 'P')
    

class Enthalpy(Physics):
  r""" 
  This class solves the internal energy balance (enthalpy) in steady state or 
  transient, and converts that solution to temperature and water content.

  Time stepping uses Crank-Nicholson, which is 2nd order accurate.
    
  :param model  : An instantiated 2D flowline ice :class:`~src.model.Model`
  :param config : Dictionary object containing information on physical 
                 	attributes such as velocties, age, and surface climate
	
  The enthalpy equation used in this class is a typical advection-diffusion 
  equation with a non-linear diffusivity

  :Enthalpy:
     .. math::
      \rho\left(\partial_t+\textbf{u}\cdot\nabla\right)H = 
      \rho\nabla\cdot\kappa\left(H\right)\nabla H + Q
		 
  +-------------------------+---------------------------------------------+
  |Term                     |Description                                  |
  +=========================+=============================================+
  |.. math::                |                                             |
  |   H                     |Enthalpy                                     |
  +-------------------------+---------------------------------------------+
  |.. math::                |                                             |
  |   \rho                  |Ice density                                  |
  +-------------------------+---------------------------------------------+
  |.. math::                |Strain heat generated by viscious dissipation|
  |   Q                     |given by the first term in the Stokes'       |
  |                         |functional                                   |
  +-------------------------+---------------------------------------------+
  |.. math::                |Ice velocity                                 |
  |   \textbf{u}            |                                             | 
  +-------------------------+---------------------------------------------+
  |.. math::                |Enthalpy dependent diffusivity               |
  |   \kappa                |                                             |
  |                         +--------------+------------------------------+
  |                         |if the ice is |.. math::                     |
  |                         |cold          |   \frac{k}{\rho C_p}         |
  |                         +--------------+------------------------------+
  |                         |if the ice is |.. math::                     |
  |                         |temperate     |   \frac{\nu}{\rho}           |	
  +-------------------------+--------------+------------------------------+
  |.. math::                |Thermal conductivity of cold ice             |
  |   k                     |                                             |
  +-------------------------+---------------------------------------------+
  |.. math::                |Heat capacity                                |
  |   C_p                   |                                             |
  +-------------------------+---------------------------------------------+
  |.. math::                |Diffusivity of enthalpy in temperate ice     |
  |   \nu                   |                                             |
  +-------------------------+---------------------------------------------+
  
  +-----------------------------------------------------------------------+	
  |Ice Definitions                                                        |
  +====================+==================================================+
  |Cold ice            |.. math::                                         |
  |                    |   \left(H-h_i\left(P\right)\right) < 0           |
  +--------------------+--------------------------------------------------+
  |Temperate ice       |.. math::                                         |
  |                    |   \left(H-h_i\left(P\right)\right) \geq 0        |
  +--------------------+--------------------------------------------------+

  +------------------------+----------------------------------------------+
  |Term                    |Definition                                    |
  +========================+==============================================+
  |.. math::               |Pressure melting point expressed in enthalpy  |
  |   h_i\left(P\right)=   |                                              |
  |   -L+C_w\left(273-     |                                              |
  |   \gamma P\right)      |                                              |
  +------------------------+----------------------------------------------+
  |.. math::               |Latent heat of fusion                         |
  |   L                    |                                              |
  +------------------------+----------------------------------------------+
  |.. math::               |Heat capacity of liquid water                 |
  |   C_w                  |                                              |
  +------------------------+----------------------------------------------+
  |.. math::               |Dependence of the melting point on pressure   |
  |   \gamma               |                                              |
  +------------------------+----------------------------------------------+
  |.. math::               |Pressure                                      |
  |   P                    |                                              |
  +------------------------+----------------------------------------------+
  
  **Stabilization**
  
  The enthalpy equation is hyperbolic and so the 
  standard centered Galerkin Finite Element method is non-optimal and 
  spurious oscillations can arise. In order to stabilize it, we apply 
  streamline upwind Petrov-Galerkin methods. 
  This consists of adding an additional diffusion term of the form
  
  :Term:
     .. math::
      \rho\nabla\cdot K\nabla H
      
  +--------------------------------+--------------------------------------+
  |Term                            |Description                           |
  +================================+======================================+
  |.. math::                       |Tensor valued diffusivity             |
  |   K_{ij} = \frac{\alpha h}{2}  |                                      |
  |   \frac{u_i u_j}{| |u| |}      |                                      |
  +--------------------------------+--------------------------------------+
  |.. math::                       |Taken to be equal to unity            |
  |   \alpha                       |                                      |
  +--------------------------------+--------------------------------------+
  |.. math::                       |Cell size metric                      |
  |   h                            |                                      |
  +--------------------------------+--------------------------------------+
  
  Alternatively, to weight the advective portion of the governing equation
  we can view this stabilization as using skewed finite element test 
  functions of the form
  
  :Equation:
     .. math::
      \hat{\phi} = \phi + \frac{\alpha h}{2}\frac{u_i u_j}{| |u| |}
      \cdot\nabla_{| |}\phi
  """
  def __init__(self, model, config):
    """ 
    Set up equation, memory allocation, etc. 
    """
    s    = "::: INITIALIZING ENTHALPY PHYSICS :::"
    print_text(s, self.color())

    self.config = config
    self.model  = model

    r           = config['velocity']['r']
    mesh        = model.mesh
    V           = model.V
    Q           = model.Q
    H           = model.H
    H0          = model.H0
    n           = model.n
    b_gnd       = model.b_gnd
    b_gnd       = model.b_gnd
    b_shf       = model.b_shf
    T           = model.T
    T0          = model.T0
    Mb          = model.Mb
    L           = model.L
    ci          = model.ci
    cw          = model.cw
    T_w         = model.T_w
    gamma       = model.gamma
    S           = model.S
    B           = model.B
    H           = S - B
    x           = model.x
    E           = model.E
    W           = model.W
    R           = model.R
    epsdot      = model.epsdot
    eps_reg     = model.eps_reg
    rhoi        = model.rhoi
    rhow        = model.rhow
    g           = model.g
    beta        = model.beta
    u           = model.u
    v           = model.v
    w           = model.w
    kappa       = model.kappa
    Kcoef       = model.Kcoef
    ki          = model.ki
    kw          = model.kw
    T_surface   = model.T_surface
    H_surface   = model.H_surface
    H_float     = model.H_float
    q_geo       = model.q_geo
    Hhat        = model.Hhat
    uhat        = model.uhat
    vhat        = model.vhat
    what        = model.what
    mhat        = model.mhat
    spy         = model.spy
    ds          = model.ds
    dSrf        = ds(2)         # surface
    dGnd        = ds(3)         # grounded bed
    dFlt        = ds(5)         # floating bed
    dSde        = ds(4)         # sides
    dBed        = dGnd + dFlt   # bed
    dx          = model.dx
    dx_s        = dx(1)
    dx_g        = dx(0)
    dx          = dx(1) + dx(0) # entire internal
    
    # second invariant of the strain-rate tensor squared :
    term   = + 0.5*(   (u.dx(1) + v.dx(0))**2  \
                     + (u.dx(2) + w.dx(0))**2  \
                     + (v.dx(2) + w.dx(1))**2) \
             + u.dx(0)**2 + v.dx(1)**2 + w.dx(2)**2 
    epsdot = 0.5 * term + eps_reg
    
    # initialize the conductivity coefficient for entirely cold ice :
    model.assign_variable(Kcoef, 1.0)

    # Define test and trial functions       
    psi = TestFunction(Q)
    dH  = TrialFunction(Q)

    # Pressure melting point
    s    = "::: calculating pressure-melting temperature :::"
    print_text(s, self.color())
    model.assign_variable(T0, project(T_w - gamma * (S - x[2]), Q))
    print_min_max(T0, 'T0')
   
    # Surface boundary condition
    model.assign_variable(H_surface, project(T_surface * ci))
    model.assign_variable(H_float,   project(ci*T0))

    # For the following heat sources, note that they differ from the 
    # oft-published expressions, in that they are both multiplied by constants.
    # I think that this is the correct form, as they must be this way in order 
    # to conserve energy.  This also implies that heretofore, models have been 
    # overestimating frictional heat, and underestimating strain heat.

    # Frictional heating :
    q_friction = 0.5 * beta**2 * H**r * (u**2 + v**2)

    # Strain heating = stress*strain
    Q_s_gnd = (2*n)/(n+1) * b_gnd * epsdot**((n+1)/(2*n))
    Q_s_shf = (2*n)/(n+1) * b_shf * epsdot**((n+1)/(2*n))

    # thermal conductivity (Greve and Blatter 2009) :
    ki    =  9.828 * exp(-0.0057*T)
    
    # bulk properties :
    k     =  (1 - W)*ki   + W*kw     # bulk thermal conductivity
    c     =  (1 - W)*ci   + W*cw     # bulk heat capacity
    rho   =  (1 - W)*rhoi + W*rhow   # bulk density
    kappa =  k / (rho*c)             # bulk thermal diffusivity

    # configure the module to run in steady state :
    if config['mode'] == 'steady':
      try:
        U    = as_vector([u, v, w])
      except NameError:
        s =  "No velocity field found.  Defaulting to no velocity"
        print_text(s, self.color())
        U    = 0.0

      # skewed test function in areas with high velocity :
      h      = CellSize(mesh)
      Unorm  = sqrt(dot(U, U) + DOLFIN_EPS)
      PE     = Unorm*h/(2*kappa)
      tau    = 1/tanh(PE) - 1/PE
      T_c    = conditional( lt(Unorm, 4), 0.0, 1.0 )
      psihat = psi + T_c*h*tau/(2*Unorm) * dot(U, grad(psi))

      # residual of model :
      self.a = + rho * dot(U, grad(dH)) * psihat * dx \
               + rho * spy * kappa * dot(grad(psi), grad(dH)) * dx \
      
      self.L = + (q_geo + q_friction) * psihat * dGnd \
               + Q_s_gnd * psihat * dx_g \
               + Q_s_shf * psihat * dx_s
      

    # configure the module to run in transient mode :
    elif config['mode'] == 'transient':
      dt = config['time_step']
    
      # Skewed test function.  Note that vertical velocity has 
      # the mesh velocity subtracted from it.
      U = as_vector([uhat, vhat, what - mhat])

      h      = CellSize(mesh)
      Unorm  = sqrt(dot(U, U) + 1.0)
      PE     = Unorm*h/(2*kappa)
      tau    = 1/tanh(PE) - 1/PE
      T_c    = conditional( lt(Unorm, 4), 0.0, 1.0 )
      psihat = psi + T_c*h*tau/(2*Unorm) * dot(U, grad(psi))

      theta = 0.5
      # Crank Nicholson method
      Hmid = theta*dH + (1 - theta)*H0
      
      # implicit system (linearized) for enthalpy at time H_{n+1}
      self.a = + rho * (dH - H0) / dt * psi * dx \
               + rho * dot(U, grad(Hmid)) * psihat * dx \
               + rho * spy * kappa * dot(grad(psi), grad(Hmid)) * dx \
      
      self.L = + (q_geo + q_friction) * psi * dGnd \
               + Q_s_gnd * psihat * dx_g \
               + Q_s_shf * psihat * dx_s
    
    # surface boundary condition : 
    self.bc_H = []
    self.bc_H.append( DirichletBC(Q, H_surface, model.ff, 2) )
    
    # apply T_w conditions of portion of ice in contact with water :
    if model.mask != None:
      self.bc_H.append( DirichletBC(Q, H_float,   model.ff, 5) )
      self.bc_H.append( DirichletBC(Q, H_surface, model.ff, 6) )
    
    # apply lateral boundaries if desired : 
    if config['enthalpy']['lateral_boundaries'] is not None:
      lat_bc = config['enthalpy']['lateral_boundaries']
      self.bc_H.append( DirichletBC(Q, lat_bc, model.ff, 4) )

    self.c          = c
    self.k          = k
    self.rho        = rho
    self.kappa      = kappa
    self.q_friction = q_friction
    self.dBed       = dBed
     
  
  def solve(self, H0=None, Hhat=None, uhat=None, 
            vhat=None, what=None, mhat=None):
    r""" 
    Uses boundary conditions and the linear solver to solve for temperature
    and water content.
    
    :param H0     : Initial enthalpy
    :param Hhat   : Enthalpy expression
    :param uhat   : Horizontal velocity
    :param vhat   : Horizontal velocity perpendicular to :attr:`uhat`
    :param what   : Vertical velocity
    :param mhat   : Mesh velocity
  
    
    A Neumann boundary condition is imposed at the basal boundary.
    
    :Boundary Condition:
       .. math::
        \kappa\left(H\right)\nabla H\cdot\textbf{n} = q_g+q_f
        -M_b\rho L
        
    +----------------------------+-------------------------------------------+
    |Terms                       |Description                                |
    +============================+===========================================+
    |.. math::                   |Geothermal heat flux, assumed to be known  |
    |   q_g                      |                                           |
    +----------------------------+-------------------------------------------+
    |.. math::                   |Frictional heat generated by basal sliding |
    |   q_f                      |                                           |
    +----------------------------+-------------------------------------------+
    |.. math::                   |Basal melt rate                            |
    |   M_b                      |                                           |
    +----------------------------+-------------------------------------------+
    
    Since temperature is uniquely related to enthalpy, it can be extracted 
    using the following equations
  
    +-----------------------------------------------------------------------+
    |                                                                       |
    +=================+=================================+===================+
    |.. math::        |.. math::                        |If the ice is cold |
    |   T\left(H,P    |   C_{p}^{-1}\left(H-h_i\left(P  |                   |
    |   \right) =     |   \right)\right)+T_{m}(p)       |                   |
    |                 +---------------------------------+-------------------+
    |                 |.. math::                        |If the ice is      |
    |                 |   T_{m}                         |temperate          |
    +-----------------+---------------------------------+-------------------+
    
    Similarly, the water content can also be extracted using the following 
    equations
    
    +-----------------------------------------------------------------------+
    |                                                                       |
    +=================+=================================+===================+
    |.. math::        |.. math::                        |If the ice is cold |
    |   \omega\left(  |   0                             |                   |
    |   H,P\right)=   |                                 |                   |
    |                 +---------------------------------+-------------------+
    |                 |.. math::                        |If the ice is      |
    |                 |   \frac{H-h_i\left(P\right)}    |temperate          |
    |                 |   {L}                           |                   |
    +-----------------+---------------------------------+-------------------+
    
    +---------------------------+-------------------------------------------+
    |Term                       |Description                                |
    +===========================+===========================================+
    |.. math::                  |Temperature melting point expressed in     |
    |   T_{m}                   |enthalpy                                   |
    +---------------------------+-------------------------------------------+
    """
    model  = self.model
    config = self.config
    
    # Assign values for H0,u,w, and mesh velocity
    if H0 is not None:
      model.assign_variable(model.H0,   H0)
      model.assign_variable(model.Hhat, Hhat)
      model.assign_variable(model.uhat, uhat)
      model.assign_variable(model.vhat, vhat)
      model.assign_variable(model.what, what)
      model.assign_variable(model.mhat, mhat)
      
    mesh       = model.mesh
    V          = model.V
    Q          = model.Q
    T0         = model.T0
    H          = model.H
    T          = model.T
    Mb         = model.Mb
    W          = model.W
    W0         = model.W0
    W_r        = model.W_r
    L          = model.L
    Kcoef      = model.Kcoef
    q_geo      = model.q_geo
    B          = model.B
    ci         = model.ci
    rhoi       = model.rhoi
    dBed       = self.dBed
    q_friction = self.q_friction
    rho        = self.rho
    kappa      = self.kappa
    
    # solve the linear equation for enthalpy :
    s    = "::: solving enthalpy :::"
    print_text(s, self.color())
    sm = config['enthalpy']['solve_method']
    solve(self.a == self.L, H, self.bc_H,
          solver_parameters = {"linear_solver" : sm})
    print_min_max(H, 'H')

    # temperature solved diagnostically : 
    s = "::: calculating temperature :::"
    print_text(s, self.color())
    T_n  = project(H/ci, Q)
    
    # update temperature for wet/dry areas :
    T_n_v        = T_n.vector().array()
    T0_v         = T0.vector().array()
    warm         = T_n_v >= T0_v
    cold         = T_n_v <  T0_v
    T_n_v[warm]  = T0_v[warm]
    model.assign_variable(T, T_n_v)
    print_min_max(T,  'T')
    
    # update kappa coefficient for wet/dry areas :
    #Kcoef_v       = Kcoef.vector().array()
    #Kcoef_v[warm] = 1.0/10.0              # wet ice
    #Kcoef_v[cold] = 1.0                   # cold ice
    #model.assign_variable(Kcoef, Kcoef_v)

    # water content solved diagnostically :
    s = "::: calculating water content :::"
    print_text(s, self.color())
    W_n  = project((H - ci*T0)/L, Q)
    
    # update water content :
    W_v             = W_n.vector().array()
    W_v[cold]       = 0.0
    W_v[W_v > 1.00] = 1.00
    model.assign_variable(W0, W)
    model.assign_variable(W,  W_v)
    print_min_max(W,  'W')
    
    # update capped variable for rheology : 
    W_v[W_v > 0.01] = 0.01
    model.assign_variable(W_r, W_v)
    
    # calculate melt-rate : 
    s = "::: calculating basal melt-rate :::"
    print_text(s, self.color())
    nMb   = project(-(q_geo + q_friction) / (L*rhoi))
    model.assign_variable(Mb,  nMb)
    print_min_max(Mb, 'Mb')

    # calculate bulk density :
    s = "::: calculating bulk density :::"
    print_text(s, self.color())
    rho       = project(self.rho)
    model.rho = rho
    print_min_max(rho,'rho')


class EnthalpyDG(Physics):
  r""" 
  """
  def __init__(self, model, config):
    """ 
    Set up equation, memory allocation, etc. 
    """
    s    = "::: INITIALIZING DG ENTHALPY PHYSICS :::"
    print_text(s, self.color())

    self.config = config
    self.model  = model

    r           = config['velocity']['r']
    mesh        = model.mesh
    Q           = model.Q
    DQ          = model.DQ
    H           = model.H
    H0          = model.H0
    n           = model.n
    b_gnd       = model.b_gnd
    b_gnd       = model.b_gnd
    b_shf       = model.b_shf
    T           = model.T
    T0          = model.T0
    Mb          = model.Mb
    L           = model.L
    ci          = model.ci
    cw          = model.cw
    T_w         = model.T_w
    gamma       = model.gamma
    S           = model.S
    B           = model.B
    x           = model.x
    E           = model.E
    W           = model.W
    R           = model.R
    epsdot      = model.epsdot
    eps_reg     = model.eps_reg
    rhoi        = model.rhoi
    rhow        = model.rhow
    g           = model.g
    beta        = model.beta
    u           = model.u
    v           = model.v
    w           = model.w
    kappa       = model.kappa
    Kcoef       = model.Kcoef
    ki          = model.ki
    kw          = model.kw
    T_surface   = model.T_surface
    H_surface   = model.H_surface
    H_float     = model.H_float
    q_geo       = model.q_geo
    Hhat        = model.Hhat
    uhat        = model.uhat
    vhat        = model.vhat
    what        = model.what
    mhat        = model.mhat
    spy         = model.spy
    ds          = model.ds
    dSrf        = ds(2)         # surface
    dGnd        = ds(3)         # grounded bed
    dFlt        = ds(5)         # floating bed
    dSde        = ds(4)         # sides
    dBed        = dGnd + dFlt   # bed
    dGamma      = ds(2) + ds(3) + ds(5) + ds(4)
    dx          = model.dx
    dx_s        = dx(1)
    dx_g        = dx(0)
    dx          = dx(1) + dx(0) # entire internal
    
    # second invariant of the strain-rate tensor squared :
    term   = + 0.5*(   (u.dx(1) + v.dx(0))**2  \
                     + (u.dx(2) + w.dx(0))**2  \
                     + (v.dx(2) + w.dx(1))**2) \
             + u.dx(0)**2 + v.dx(1)**2 + w.dx(2)**2 
    epsdot = 0.5 * term + eps_reg
    
    # If we're not using the output of the surface climate model,
    #  set the surface temperature to the constant or array that 
    #  was passed in.
    if not config['enthalpy']['use_surface_climate']:
      T_surface = Function(DQ)
      model.assign_variable(T_surface, config['enthalpy']['T_surface'])

    # assign geothermal flux :
    q_geo = Function(DQ)
    model.assign_variable(q_geo, config['enthalpy']['q_geo'])

    # Define test and trial functions       
    psi = TestFunction(DQ)
    dH  = TrialFunction(DQ)

    # Pressure melting point
    T0 = Function(DQ)
    model.assign_variable(T0, project(T_w - gamma * (S - x[2]), DQ))
   
    # Surface boundary condition
    H_surface = Function(DQ)
    H_float   = Function(DQ)
    model.assign_variable(H_surface, project(T_surface * ci, DQ))
    model.assign_variable(H_float,   project(ci*T0, DQ))

    # For the following heat sources, note that they differ from the 
    # oft-published expressions, in that they are both multiplied by constants.
    # I think that this is the correct form, as they must be this way in order 
    # to conserve energy.  This also implies that heretofore, models have been 
    # overestimating frictional heat, and underestimating strain heat.

    # Frictional heating :
    q_friction = 0.5 * beta**2 * (S - B)**r * (u**2 + v**2)

    # Strain heating = stress*strain
    Q_s_gnd = (2*n)/(n+1) * b_gnd * epsdot**((n+1)/(2*n))
    Q_s_shf = (2*n)/(n+1) * b_shf * epsdot**((n+1)/(2*n))

    # thermal conductivity (Greve and Blatter 2009) :
    ki    =  9.828 * exp(-0.0057*T)
    
    # bulk properties :
    k     =  (1 - W)*ki   + W*kw     # bulk thermal conductivity
    c     =  (1 - W)*ci   + W*cw     # bulk heat capacity
    rho   =  (1 - W)*rhoi + W*rhow   # bulk density
    kappa =  k / (rho*c)             # bulk thermal diffusivity

    # configure the module to run in steady state :
    if config['mode'] == 'steady':
      try:
        U    = as_vector([u, v, w])
      except NameError:
        print "No velocity field found.  Defaulting to no velocity"
        U    = 0.0

      h      = CellSize(mesh)
      n      = FacetNormal(mesh)
      h_avg  = (h('+') + h('-'))/2.0
      un     = (dot(U, n) + abs(dot(U, n)))/2.0
      alpha  = Constant(5.0)

      # residual of model :
      a_int  = rho * dot(grad(psi), spy * kappa*grad(dH) - U*dH)*dx
             
      a_fac  = + rho * spy * kappa * (alpha / h_avg)*jump(psi)*jump(dH) * dS \
               - rho * spy * kappa * dot(avg(grad(psi)), jump(dH, n)) * dS \
               - rho * spy * kappa * dot(jump(psi, n), avg(grad(dH))) * dS
                 
      a_vel  = jump(psi)*jump(un*dH)*dS  + psi*un*dH*dGamma
      
      self.a = a_int + a_fac + a_vel

      #self.a = + rho * dot(U, grad(dH)) * psihat * dx \
      #         + rho * spy * kappa * dot(grad(psi), grad(dH)) * dx \
      
      self.L = + (q_geo + q_friction) * psi * dGnd \
               + Q_s_gnd * psi * dx_g \
               + Q_s_shf * psi * dx_s
      

    # configure the module to run in transient mode :
    elif config['mode'] == 'transient':
      dt = config['time_step']
    
      # Skewed test function.  Note that vertical velocity has 
      # the mesh velocity subtracted from it.
      U = as_vector([uhat, vhat, what - mhat])

      h      = CellSize(mesh)
      Unorm  = sqrt(dot(U, U) + 1.0)
      PE     = Unorm*h/(2*kappa)
      tau    = 1/tanh(PE) - 1/PE
      T_c    = conditional( lt(Unorm, 4), 0.0, 1.0 )
      psihat = psi + T_c*h*tau/(2*Unorm) * dot(U, grad(psi))

      theta = 0.5
      # Crank Nicholson method
      Hmid = theta*dH + (1 - theta)*H0
      
      # implicit system (linearized) for enthalpy at time H_{n+1}
      self.a = + rho * (dH - H0) / dt * psi * dx \
               + rho * dot(U, grad(Hmid)) * psihat * dx \
               + rho * spy * kappa * dot(grad(psi), grad(Hmid)) * dx \
      
      self.L = + (q_geo + q_friction) * psi * dGnd \
               + Q_s_gnd * psihat * dx_g \
               + Q_s_shf * psihat * dx_s

    model.H         = Function(DQ)
    model.T_surface = T_surface
    model.q_geo     = q_geo
    model.T0        = T0
    model.H_surface = H_surface
    model.H_float   = H_float
    
    self.c          = c
    self.k          = k
    self.rho        = rho
    self.kappa      = kappa
    self.q_friction = q_friction
    self.dBed       = dBed
     
  
  def solve(self, H0=None, Hhat=None, uhat=None, 
            vhat=None, what=None, mhat=None):
    r""" 
    """
    model  = self.model
    config = self.config
    
    # Assign values for H0,u,w, and mesh velocity
    if H0 is not None:
      model.assign_variable(model.H0,   H0)
      model.assign_variable(model.Hhat, Hhat)
      model.assign_variable(model.uhat, uhat)
      model.assign_variable(model.vhat, vhat)
      model.assign_variable(model.what, what)
      model.assign_variable(model.mhat, mhat)
      
    lat_bc     = config['enthalpy']['lateral_boundaries']
    mesh       = model.mesh
    V          = model.V
    Q          = model.Q
    DQ         = model.DQ
    T0         = model.T0
    H          = model.H
    H_surface  = model.H_surface
    H_float    = model.H_float  
    T          = model.T
    Mb         = model.Mb
    W          = model.W
    W0         = model.W0
    W_r        = model.W_r
    L          = model.L
    Kcoef      = model.Kcoef
    q_geo      = model.q_geo
    B          = model.B
    ci         = model.ci
    rhoi       = model.rhoi
    dBed       = self.dBed
    q_friction = self.q_friction
    rho        = self.rho
    kappa      = self.kappa

    # surface boundary condition : 
    self.bc_H = []
    self.bc_H.append( DirichletBC(DQ, H_surface, model.ff, 2) )
    
    # apply T_w conditions of portion of ice in contact with water :
    if model.mask != None:
      self.bc_H.append( DirichletBC(DQ, H_float,   model.ff, 5) )
      self.bc_H.append( DirichletBC(DQ, H_surface, model.ff, 6) )
    
    # apply lateral boundaries if desired : 
    if config['enthalpy']['lateral_boundaries'] is not None:
      self.bc_H.append( DirichletBC(DQ, lat_bc, model.ff, 4) )
    
    # solve the linear equation for enthalpy :
    if self.model.MPI_rank==0:
      s    = "::: solving DG internal energy :::"
      text = colored(s, 'cyan')
      print text
    sm = config['enthalpy']['solve_method']
    solve(self.a == self.L, H, self.bc_H, 
          solver_parameters = {"linear_solver" : sm})

    # calculate temperature and water content :
    s = "::: calculating temperature, water content, and basal melt-rate :::"
    print_text(s, self.color())
    
    # temperature solved diagnostically : 
    T_n  = project(H/ci, Q)
    
    # update temperature for wet/dry areas :
    T_n_v        = T_n.vector().array()
    T0_v         = T0.vector().array()
    warm         = T_n_v >= T0_v
    cold         = T_n_v <  T0_v
    T_n_v[warm]  = T0_v[warm]
    model.assign_variable(T, T_n_v)
    
    # water content solved diagnostically :
    W_n  = project((H - ci*T0)/L, Q)
    
    # update water content :
    W_v        = W_n.vector().array()
    W_v[cold]  = 0.0
    model.assign_variable(W0, W)
    model.assign_variable(W,  W_v)
    
    # update capped variable for rheology : 
    W_v[W_v > 0.01] = 0.01
    model.assign_variable(W_r, W_v)
    
    # calculate melt-rate : 
    nMb   = project(-(q_geo + q_friction) / (L*rhoi))
    model.assign_variable(Mb,  nMb)

    # calculate bulk density :
    rho       = project(self.rho)
    model.rho = rho
    
    # print the min/max values to the screen :    
    print_min_max(H,  'H')
    print_min_max(T,  'T')
    print_min_max(Mb, 'Mb')
    print_min_max(W,  'W')
    print_min_max(rho,'rho')


class FreeSurface(Physics):
  r""" 
  Class for evolving the free surface of the ice through time.
  
  :param model  : An instantiated 2D flowline ice :class:`~src.model.Model`
  :param config : Dictionary object containing information on physical 
                 	attributes such as velocties, age, and surface climate

  **Stabilization** 

  The free surface equation is hyperbolic so, a modified Galerkin test 
  function is used for stabilization.
  
  :Equation:
     .. math::
      \hat{\phi} = \phi + \frac{\alpha h}{2}\frac{u_i u_j}{| |u| |}
      \cdot\nabla_{| |}\phi
     
  A shock-capturing artificial viscosity is applied in order to smooth the 
  sharp discontinuities that occur at the ice boundaries where the model
  domain switches from ice to ice-free regimes.  The additional term is
  given by
  
  :Equation:
     .. math::
      D_{shock} = \nabla \cdot C \nabla S

  +-----------------------------------+-----------------------------------+
  |Term                               |Description                        |
  +===================================+===================================+
  |.. math::                          |Nonlinear residual-dependent scalar|
  |   C = \frac{h}{2| |u| |}\left[    |                                   |
  |   \nabla_{| |}S\cdot\nabla        |                                   |
  |   _{| |}S\right]^{-1}\mathcal{R}  |                                   |
  |   ^{2}                            |                                   |
  +-----------------------------------+-----------------------------------+
  |.. math::                          |Residual of the original free      |
  |   \mathcal{R}                     |surface equation                   |
  +-----------------------------------+-----------------------------------+

  For the Stokes' equations to remain stable, it is necessary to either
  satisfy or circumvent the Ladyzehnskaya-Babuska-Brezzi (LBB) condition.
  We circumvent this condition by using a Galerkin-least squares (GLS)
  formulation of the Stokes' functional:
    
  :Equation:
     .. math::
      \mathcal{A}'\left[\textbf{u},P\right] = \mathcal{A} - \int
      \limits_\Omega\tau_{gls}\left(\nabla P - \rho g\right)\cdot
      \left(\nabla P - \rho g\right)d\Omega
      
  +----------------------------------------+------------------------------+
  |Term                                    |Description                   |
  +========================================+==============================+
  |.. math::                               |Variational principle for     |
  |   \mathcal{A}                          |power law rheology            |
  +----------------------------------------+------------------------------+
  |.. math::                               |Pressure                      |
  |   P                                    |                              |
  +----------------------------------------+------------------------------+
  |.. math::                               |Ice density                   |
  |   \rho                                 |                              |
  +----------------------------------------+------------------------------+
  |.. math::                               |Force of gravity              |
  |   g                                    |                              |
  +----------------------------------------+------------------------------+
  |.. math::                               |Stabilization parameter. Since|
  |   \tau_{gls} = \frac{h^2}              |it is a function of ice       |
  |   {12\rho b(T)}                        |viscosity, the stabilization  |
  |                                        |parameter is nonlinear        |
  +----------------------------------------+------------------------------+
  |.. math::                               |Temperature dependent rate    |
  |   b(T)                                 |factor                        |
  +----------------------------------------+------------------------------+
  """

  def __init__(self, model, config):
    """
    """
    s    = "::: INITIALIZING FREE-SURFACE PHYSICS :::"
    print_text(s, self.color())

    self.model  = model
    self.config = config

    Q_flat = model.Q_flat
    Q      = model.Q

    phi    = TestFunction(Q_flat)
    dS     = TrialFunction(Q_flat)

    self.Shat   = model.Shat           # surface elevation velocity 
    self.ahat   = model.ahat           # accumulation velocity
    self.uhat   = model.uhat_f         # horizontal velocity
    self.vhat   = model.vhat_f         # horizontal velocity perp. to uhat
    self.what   = model.what_f         # vertical velocity
    mhat        = model.mhat           # mesh velocity
    dSdt        = model.dSdt           # surface height change
    M           = model.M
    ds          = model.ds_flat
    dSurf       = ds(2)
    dBase       = ds(3)
    
    self.static_boundary = DirichletBC(Q, 0.0, model.ff_flat, 4)
    h = CellSize(model.flat_mesh)

    # upwinded trial function :
    unorm       = sqrt(self.uhat**2 + self.vhat**2 + 1e-1)
    upwind_term = h/(2.*unorm)*(self.uhat*phi.dx(0) + self.vhat*phi.dx(1))
    phihat      = phi + upwind_term

    mass_matrix = dS * phihat * dSurf
    lumped_mass = phi * dSurf

    stiffness_matrix = - self.uhat * self.Shat.dx(0) * phihat * dSurf \
                       - self.vhat * self.Shat.dx(1) * phihat * dSurf\
                       + (self.what + self.ahat) * phihat * dSurf
    
    # Calculate the nonlinear residual dependent scalar
    term1            = self.Shat.dx(0)**2 + self.Shat.dx(1)**2 + 1e-1
    term2            = + self.uhat*self.Shat.dx(0) \
                       + self.vhat*self.Shat.dx(1) \
                       - (self.what + self.ahat)
    C                = 10.0*h/(2*unorm) * term1 * term2**2
    diffusion_matrix = C * dot(grad(phi), grad(self.Shat)) * dSurf
    
    # Set up the Galerkin-least squares formulation of the Stokes' functional
    A_pro         = - phi.dx(2)*dS*dx - dS*phi*dBase + dSdt*phi*dSurf 
    M.vector()[:] = 1.0
    self.M        = M*dx

    self.newz                   = Function(model.Q)
    self.mass_matrix            = mass_matrix
    self.stiffness_matrix       = stiffness_matrix
    self.diffusion_matrix       = diffusion_matrix
    self.lumped_mass            = lumped_mass
    self.A_pro                  = A_pro
    
  def solve(self):
    """
    :param uhat : Horizontal velocity
    :param vhat : Horizontal velocity perpendicular to :attr:`uhat`
    :param what : Vertical velocity 
    :param Shat : Surface elevation velocity
    :param ahat : Accumulation velocity

    """
    model  = self.model
    config = self.config
   
    model.assign_variable(self.Shat, model.S) 
    model.assign_variable(self.ahat, model.smb) 
    model.assign_variable(self.uhat, model.u) 
    model.assign_variable(self.vhat, model.v) 
    model.assign_variable(self.what, model.w) 

    m = assemble(self.mass_matrix,      keep_diagonal=True)
    r = assemble(self.stiffness_matrix, keep_diagonal=True)

    s    = "::: solving free-surface :::"
    print_text(s, self.color())
    if config['free_surface']['lump_mass_matrix']:
      m_l = assemble(self.lumped_mass)
      m_l = m_l.get_local()
      m_l[m_l==0.0]=1.0
      m_l_inv = 1./m_l

    if config['free_surface']['static_boundary_conditions']:
      self.static_boundary.apply(m,r)

    if config['free_surface']['use_shock_capturing']:
      k = assemble(self.diffusion_matrix)
      r -= k
      print_min_max(r, 'D')

    if config['free_surface']['lump_mass_matrix']:
      model.assign_variable(model.dSdt, m_l_inv * r.get_local())
    else:
      m.ident_zeros()
      solve(m, model.dSdt.vector(), r)

    A = assemble(lhs(self.A_pro))
    p = assemble(rhs(self.A_pro))
    q = Vector()  
    solve(A, q, p)
    model.assign_variable(model.dSdt, q)

class AdjointVelocity(Physics):
  """ 
  Complete adjoint of the BP momentum balance.  Now updated to calculate
  the adjoint model and gradient using automatic differentiation.  Changing
  the form of the objective function and the differentiation variables now
  automatically propgates through the machinery.  This means that switching
  to topography optimization, or minimization of dHdt is now straightforward,
  and requires no math.
    
  :param model  : An instantiated 2D flowline ice :class:`~src.model.Model`
  :param config : Dictionary object containing information on physical 
                  attributes such as velocties, age, and surface climate
  """
  def __init__(self, model, config):
    """ 
    Setup.
    """
    s   = "::: INITIALIZING ADJOINT VELOCITY PHYSICS :::"
    print_text(s, self.color())

    self.model  = model
    self.config = config

    Q        = model.Q
    Vd_shf   = model.Vd_shf
    Vd_gnd   = model.Vd_gnd
    Pe       = model.Pe
    Sl_shf   = model.Sl_shf
    Sl_gnd   = model.Sl_gnd
    Pb       = model.Pb
    Pc       = model.Pc
    Lsq      = model.Lsq
    Nc       = model.Nc
    U        = model.U
    u_o      = model.u_o
    v_o      = model.v_o
    adot     = model.adot
    ds       = model.ds
    S        = model.S
    
    dx       = model.dx
    dx_s     = dx(1)
    dx_g     = dx(0)
    dx       = dx(1) + dx(0) # entire internal
    dSrf_s   = ds(6)         # surface
    dSrf_g   = ds(2)         # surface
    dGnd     = ds(3)         # grounded bed 
    dFlt     = ds(5)         # floating bed
    dSde     = ds(4)         # sides
    dBed     = dGnd + dFlt   # bed

    if config['adjoint']['surface_integral'] == 'shelves':
      dSrf     = ds(6)
    elif config['adjoint']['surface_integral'] == 'grounded':
      dSrf     = ds(2)

    control = config['adjoint']['control_variable']
    alpha   = config['adjoint']['alpha']

    if config['velocity']['approximation'] == 'fo':
      Q_adj   = model.Q2
      A       = Vd_shf*dx_s + Vd_gnd*dx_g + Pe*dx + Sl_gnd*dGnd + Sl_shf*dFlt
    elif config['velocity']['approximation'] == 'stokes':
      Q_adj   = model.Q4
      A       = + Vd_shf*dx_s + Vd_gnd*dx_g + (Pe + Pc + Lsq)*dx \
                + Sl_gnd*dGnd + Sl_shf*dFlt + Nc*dGnd
    if (not config['periodic_boundary_conditions']
        and config['use_pressure_boundary']):
      A      += Pb*dSde

    # form regularization term 'R' :
    N = FacetNormal(model.mesh)
    for a,c in zip(alpha,control):
      if isinstance(a, (float,int)):
        a = Constant(0.5*a)
      else:
        a = Constant(0.5)
      if config['adjoint']['regularization_type'] == 'TV':
        R = a * sqrt(   (c.dx(0)*N[2] - c.dx(1)*N[0])**2 \
                      + (c.dx(1)*N[2] - c.dx(2)*N[1])**2 + 1e-3) * dGnd
      elif config['adjoint']['regularization_type'] == 'Tikhonov':
        R = a * (   (c.dx(0)*N[2] - c.dx(1)*N[0])**2 \
                  + (c.dx(1)*N[2] - c.dx(2)*N[1])**2) * dGnd
      else:
        s = "Valid regularizations are 'TV' and 'Tikhonov';" + \
            + " defaulting to no regularization."
        print_text(s, self.color())
        R = Constant(0.0) * dGnd
    
    # Objective function; least squares over the surface.
    if config['adjoint']['objective_function'] == 'logarithmic':
      a      = Constant(0.5)
      self.I = a * ln( (sqrt(U[0]**2 + U[1]**2) + 1.0) / \
                       (sqrt( u_o**2 +  v_o**2) + 1.0))**2 * dSrf + R
    
    elif config['adjoint']['objective_function'] == 'kinematic':
      a      = Constant(0.5)
      self.I = a * (+ U[0]*S.dx(0) + U[1]*S.dx(1) \
                    - (U[2] + adot))**2 * dSrf + R

    elif config['adjoint']['objective_function'] == 'linear':
      a      = Constant(0.5)
      self.I = a * ((U[0] - u_o)**2 + (U[1] - v_o)**2) * dSrf + R
    
    elif config['adjoint']['objective_function'] == 'log_lin_hybrid':
      g1     = Constant(0.5 * config['adjoint']['gamma1'])
      g2     = Constant(0.5 * config['adjoint']['gamma2'])
      self.I = + g1 * ((U[0] - u_o)**2 + (U[1] - v_o)**2) * dSrf \
               + g2 * ln( (sqrt(U[0]**2 + U[1]**2) + 1.0) / \
                          (sqrt( u_o**2 +  v_o**2) + 1.0))**2 * dSrf \
               + R

    else:
      s = "adjoint objection function may be 'linear', 'logarithmic'," \
          + " 'kinematic', or log_lin_hybrid."
      print_text(s, self.color())
      exit(1)

    Phi       = TestFunction(Q_adj)
    model.Lam = Function(Q_adj)
    L         = TrialFunction(Q_adj)
    
    # Derivative, with trial function L.  This is the momentum equations 
    # in weak form multiplied by L and integrated by parts
    F_adjoint = derivative(A, U, L)
    
    # Objective function constrained to obey the forward model
    I_adjoint  = self.I + F_adjoint

    # Gradient of this with respect to U in the direction of a test 
    # function yields a bilinear residual, which when solved yields the 
    # value of the adjoint variable
    self.dI    = derivative(I_adjoint, U, Phi)

    # Instead of treating the Lagrange multiplier as a trial function, treat 
    # it as a function.
    F_gradient = derivative(A, U, model.Lam)

    # This is a scalar quantity when discretized, as it contains no test or 
    # trial functions
    I_gradient = self.I + F_gradient

    # Differentiation wrt to the control variable in the direction of a test 
    # function yields a vector.  Assembly of this vector yields dJ/dbeta
    self.J = []
    rho    = TestFunction(Q)
    for c in control:
      self.J.append(derivative(I_gradient, c, rho))

  def solve(self):
    """
    Solves the bilinear residual created by differentiation of the 
    variational principle in combination with an objective function.
    """
    model = self.model

    s    = "::: solving adjoint velocity :::"
    print_text(s, self.color())

    solve(lhs(self.dI) == rhs(self.dI), model.Lam,
          solver_parameters = {"linear_solver"  : "cg",
                               "preconditioner" : "hypre_amg"})
    print_min_max(model.Lam, 'Lam')
    

class SurfaceClimate(Physics):

  """
  Class which specifies surface mass balance, surface temperature using a 
  PDD model.
  
  :param model  : An instantiated 2D flowline ice :class:`~src.model.Model`
  :param config : Dictionary object containing information on physical 
                  attributes such as velocties, age, and surface climate
  """

  def __init__(self, model, config):
    self.model  = model
    self.config = config

  def solve(self):
    """
    Calculates PDD, surface temperature given current model geometry

    """
    s    = "::: solving surface climate :::"
    print_text(s, self.color())
    model  = self.model
    config = self.config

    T_ma  = config['surface_climate']['T_ma']
    T_w   = model.T_w
    S     = model.S.vector().array()
    lat   = model.lat.vector().array()
    
    # Apply the lapse rate to the surface boundary condition
    model.assign_variable(model.T_surface, T_ma(S, lat) + T_w)


class Age(Physics):
  r"""
  Class for calculating the age of the ice in steady state.

  :Very simple PDE:
     .. math::
      \vec{u} \cdot \nabla A = 1

  This equation, however, is numerically challenging due to its being 
  hyperbolic.  This is addressed by using a streamline upwind Petrov 
  Galerkin (SUPG) weighting.
  
  :param model  : An instantiated 2D flowline ice :class:`~src.model.Model`
  :param config : Dictionary object containing information on physical 
                  attributes such as velocties, age, and surface climate
  """

  def __init__(self, model, config):
    """ 
    Set up the equations 
    """
    s    = "::: INITIALIZING AGE PHYSICS :::"
    print_text(s, self.color())

    self.model  = model
    self.config = config

    # Trial and test
    a   = TrialFunction(model.Q)
    phi = TestFunction(model.Q)

    # Steady state
    if config['mode'] == 'steady':
      # SUPG method :
      h      = CellSize(model.mesh)
      U      = as_vector([model.u, model.v, model.w])
      Unorm  = sqrt(dot(U,U) + DOLFIN_EPS)
      phihat = phi + h/(2*Unorm) * dot(U,grad(phi))
      
      # Residual 
      R = dot(U,grad(a)) - 1.0

      # Weak form of residual
      self.F = R * phihat * dx

    else:
      # Starting and midpoint quantities
      ahat   = model.ahat
      a0     = model.a0
      uhat   = model.uhat
      vhat   = model.vhat
      what   = model.what
      mhat   = model.mhat

      # Time step
      dt     = config['time_step']

      # SUPG method (note subtraction of mesh velocity) :
      h      = CellSize(model.mesh)
      U      = as_vector([uhat, vhat, what-mhat])
      Unorm  = sqrt(dot(U,U) + DOLFIN_EPS)
      phihat = phi + h/(2*Unorm) * dot(U,grad(phi))

      # Midpoint value of age for Crank-Nicholson
      a_mid = 0.5*(a + self.ahat)
      
      # Weak form of time dependent residual
      self.F = + (a - a0)/dt * phi * dx \
               + dot(U, grad(a_mid)) * phihat * dx \
               - 1.0 * phihat * dx

  def solve(self, ahat=None, a0=None, uhat=None, what=None, vhat=None):
    """ 
    Solve the system
    
    :param ahat   : Observable estimate of the age
    :param a0     : Initial age of the ice
    :param uhat   : Horizontal velocity
    :param vhat   : Horizontal velocity perpendicular to :attr:`uhat`
    :param what   : Vertical velocity
    """
    model  = self.model
    config = self.config

    # Assign values to midpoint quantities and mesh velocity
    if ahat:
      model.assign_variable(model.ahat, ahat)
      model.assign_variable(model.a0,   a0)
      model.assign_variable(model.uhat, uhat)
      model.assign_variable(model.vhat, vhat)
      model.assign_variable(model.what, what)
   
    if config['age']['use_smb_for_ela']:
      self.bc_age = DirichletBC(model.Q, 0.0, model.ff_acc, 1)
    
    else:
      def above_ela(x,on_boundary):
        return x[2] > config['age']['ela'] and on_boundary
      self.bc_age = DirichletBC(model.Q, 0.0, above_ela)

    # Solve!
    s    = "::: solving age :::"
    print_text(s, self.color())
    solve(lhs(self.F) == rhs(self.F), model.age, self.bc_age)
    print_min_max(model.age, 'age')


class VelocityBalance(Physics):
  
  def __init__(self, model, config):
    """
    """ 
    s    = "::: INITIALIZING VELOCITY-BALANCE PHYSICS :::"
    print_text(s, self.color())


    self.model  = model
    self.config = config
    
    Q           = model.Q
    g           = model.g
    rho         = model.rhoi
    S           = model.S
    B           = model.B
    H           = S - B
    dSdx        = model.dSdx
    dSdy        = model.dSdy
    d_x         = model.d_x
    d_y         = model.d_y
    kappa       = model.kappa
    adot        = model.adot
   
    # assign the variables something that the user specifies : 
    model.assign_variable(kappa, config['balance_velocity']['kappa'])
    model.assign_variable(adot,  config['balance_velocity']['adot'])
        
    #===========================================================================
    # form to calculate direction of flow (down driving stress gradient) :

    phi  = TestFunction(Q)
    Ubar = TrialFunction(Q)
    Nx   = TrialFunction(Q)
    Ny   = TrialFunction(Q)
    
    # calculate horizontally smoothed driving stress :
    a_dSdx = + Nx * phi * dx \
             + (kappa*H)**2 * (phi.dx(0)*Nx.dx(0) + phi.dx(1)*Nx.dx(1)) * dx
    L_dSdx = rho * g * H * dSdx * phi * dx \
    
    a_dSdy = + Ny * phi * dx \
             + (kappa*H)**2 * (phi.dx(0)*Ny.dx(0) + phi.dx(1)*Ny.dx(1)) * dx
    L_dSdy = rho * g * H * dSdy * phi*dx \
    
    # SUPG method :
    dS      = as_vector([d_x, d_y, 0.0])
    cellh   = CellSize(model.mesh)
    phihat  = phi + cellh/(2*H) * ((H*dS[0]*phi).dx(0) + (H*dS[1]*phi).dx(1))
    
    def L(u, uhat):
      return div(uhat)*u + dot(grad(u), uhat)
    
    B = L(Ubar*H, dS) * phihat * dx
    a = adot * phihat * dx

    self.a_dSdx = a_dSdx
    self.a_dSdy = a_dSdy
    self.L_dSdx = L_dSdx
    self.L_dSdy = L_dSdy
    self.B      = B
    self.a      = a
    
  
  def solve(self):
    """
    Solve the balance velocity.
    """
    model = self.model

    s    = "::: calculating surface gradient :::"
    print_text(s, self.color())
    
    dSdx   = project(model.S.dx(0), model.Q)
    dSdy   = project(model.S.dx(1), model.Q)
    model.assign_variable(model.dSdx, dSdx)
    model.assign_variable(model.dSdy, dSdy)
    print_min_max(model.dSdx, 'dSdx')
    print_min_max(model.dSdy, 'dSdy')
    
    # update velocity direction from driving stress :
    s    = "::: solving for smoothed x-component of driving stress :::"
    print_text(s, self.color())
    solve(self.a_dSdx == self.L_dSdx, model.Nx)
    print_min_max(model.Nx, 'Nx')
    
    s    = "::: solving for smoothed y-component of driving stress :::"
    print_text(s, self.color())
    solve(self.a_dSdy == self.L_dSdy, model.Ny)
    print_min_max(model.Ny, 'Ny')
    
    # normalize the direction vector :
    s    =   "::: calculating normalized velocity direction" \
           + " from driving stress :::"
    print_text(s, self.color())
    d_x_v = model.Nx.vector().array()
    d_y_v = model.Ny.vector().array()
    d_n_v = np.sqrt(d_x_v**2 + d_y_v**2 + 1e-16)
    model.assign_variable(model.d_x, -d_x_v / d_n_v)
    model.assign_variable(model.d_y, -d_y_v / d_n_v)
    print_min_max(model.d_x, 'd_x')
    print_min_max(model.d_y, 'd_y')
    
    # calculate balance-velocity :
    s    = "::: solving velocity balance magnitude :::"
    print_text(s, self.color())
    solve(self.B == self.a, model.Ubar)
    print_min_max(model.Ubar, 'Ubar')
    

class StokesBalance3D(Physics):

  def __init__(self, model, config):
    """
    """
    s    = "::: INITIALIZING STOKES-BALANCE PHYSICS :::"
    print_text(s, self.color())

    self.model  = model
    self.config = config

    mesh     = model.mesh
    ff       = model.ff
    Q        = model.Q
    Q2       = model.Q2
    u        = model.u
    v        = model.v
    w        = model.w
    S        = model.S
    B        = model.B
    H        = S - B
    beta     = model.beta
    eta      = model.eta
    rhoi     = model.rhoi
    rhow     = model.rhow
    g        = model.g
    x        = model.x
    eps_reg  = model.eps_reg
    
    dx       = model.dx
    dx_s     = dx(1)
    dx_g     = dx(0)
    if model.mask != None:
      dx     = dx(1) + dx(0) # entire internal
    ds       = model.ds  
    dGnd     = ds(3)         # grounded bed
    dFlt     = ds(5)         # floating bed
    dSde     = ds(4)         # sides
    dBed     = dGnd + dFlt   # bed
    
    # pressure boundary :
    class Depth(Expression):
      def eval(self, values, x):
        values[0] = min(0, x[2])
    D = Depth(element=Q.ufl_element())
    N = FacetNormal(mesh)
    
    f_w      = rhoi*g*(S - x[2]) + rhow*g*D
    
    Phi      = TestFunction(Q2)
    phi, psi = split(Phi)
    dU       = TrialFunction(Q2)
    du, dv   = split(dU)
    
    U        = as_vector([u, v])
    U_nm     = model.normalize_vector(U)
    U        = as_vector([U[0],     U[1],  ])
    U_n      = as_vector([U_nm[0],  U_nm[1]])
    U_t      = as_vector([U_nm[1], -U_nm[0]])
    
    u_s      = dot(dU, U_n)
    v_s      = dot(dU, U_t)
    U_s      = as_vector([u_s,       v_s      ])
    gradu    = as_vector([u_s.dx(0), u_s.dx(1)])
    gradv    = as_vector([v_s.dx(0), v_s.dx(1)])
    dudn     = dot(gradu, U_n)
    dudt     = dot(gradu, U_t)
    dudz     = u_s.dx(2)
    dvdn     = dot(gradv, U_n)
    dvdt     = dot(gradv, U_t)
    dvdz     = v_s.dx(2)
    gradphi  = as_vector([phi.dx(0), phi.dx(1)])
    gradpsi  = as_vector([psi.dx(0), psi.dx(1)])
    gradS    = as_vector([S.dx(0),   S.dx(1)  ])
    dphidn   = dot(gradphi, U_n)
    dphidt   = dot(gradphi, U_t)
    dpsidn   = dot(gradpsi, U_n)
    dpsidt   = dot(gradpsi, U_t)
    dSdn     = dot(gradS,   U_n)
    dSdt     = dot(gradS,   U_t)
    gradphi  = as_vector([dphidn,    dphidt,  phi.dx(2)])
    gradpsi  = as_vector([dpsidn,    dpsidt,  psi.dx(2)])
    gradS    = as_vector([dSdn,      dSdt,    S.dx(2)  ])
    
    epi_1  = as_vector([2*dudn + dvdt, 
                        0.5*(dudt + dvdn),
                        0.5*dudz             ])
    epi_2  = as_vector([0.5*(dudt + dvdn),
                             dudn + 2*dvdt,
                        0.5*dvdz             ])
    
    if   config['stokes_balance']['viscosity_mode'] == 'isothermal':
      A = config['stokes_balance']['A']
      s = "::: using isothermal visosity formulation :::"
      print_text(s, self.color())
      print_min_max(A, 'A')
      b     = Constant(A**(-1/n))
    
    elif config['stokes_balance']['viscosity_mode'] == 'linear':
      b = config['stokes_balance']['eta']
      s = "::: using linear visosity formulation :::"
      print_text(s, self.color())
      print_min_max(eta, 'eta')
      n     = 1.0
    
    elif config['stokes_balance']['viscosity_mode'] == 'full':
      s     = "::: using full visosity formulation :::"
      print_text(s, self.color())
      model.assign_variable(model.E, config['stokes_balance']['E'])
      model.assign_variable(model.T, config['stokes_balance']['T'])
      model.assign_variable(model.W, config['stokes_balance']['W'])
      R     = model.R
      T     = model.T
      E     = model.E
      W     = model.W
      a_T   = conditional( lt(T, 263.15), 1.1384496e-5, 5.45e10)
      Q_T   = conditional( lt(T, 263.15), 6e4,          13.9e4)
      b     = ( E*(a_T*(1 + 181.25*W))*exp(-Q_T/(R*T)) )**(-1/n)
    
    term   = 0.5 * (0.5 * (u.dx(2)**2 + v.dx(2)**2 + (u.dx(1) + v.dx(0))**2) \
                    + u.dx(0)**2 + v.dx(1)**2 + (u.dx(0) + v.dx(1))**2 )
    epsdot = term + eps_reg
    eta    = b * epsdot**((1-n)/(2*n))
    
    tau_dn = phi * rhoi * g * gradS[0] * dx
    tau_dt = psi * rhoi * g * gradS[1] * dx
    
    tau_bn = - beta**2 * u_s * phi * dBed
    tau_bt = - beta**2 * v_s * psi * dBed
    
    tau_pn = f_w * N[0] * phi * dSde
    tau_pt = f_w * N[1] * psi * dSde
    
    tau_1  = - 2 * eta * dot(epi_1, gradphi) * dx
    tau_2  = - 2 * eta * dot(epi_2, gradpsi) * dx
    
    tau_n  = tau_1 + tau_bn + tau_pn - tau_dn
    tau_t  = tau_2 + tau_bt + tau_pt - tau_dt
    
    delta  = tau_n + tau_t
    U_s    = Function(Q2)

    # make the variables available to solve :
    self.delta = delta
    self.U_s   = U_s
    self.U_n   = U_n
    self.U_t   = U_t
    self.N     = N
    self.f_w   = f_w
    
  def solve(self):
    """
    """
    model = self.model
    delta = self.delta
    U_s   = self.U_s
    
    s    = "::: solving '3D-stokes-balance' for flow direction :::"
    print_text(s, self.color())
    solve(lhs(delta) == rhs(delta), U_s)
    model.u_s, model.v_s = U_s.split(True)
    print_min_max(model.u_s, 'u_s')
    print_min_max(model.v_s, 'v_s')

  def component_stress_stokes(self):  
    """
    """
    model  = self.model
    config = self.config
    
    s    = "solving '3D-stokes-balance' for stress terms :::" 
    print_text(s, self.color())

    outpath = config['output_path']
    Q       = model.Q
    N       = self.N
    beta    = model.beta
    eta     = model.eta
    S       = model.S
    B       = model.B
    H       = S - B
    rhoi    = model.rhoi
    g       = model.g
    
    dx      = model.dx
    dx_s    = dx(1)
    dx_g    = dx(0)
    if model.mask != None:
      dx    = dx(1) + dx(0) # entire internal
    ds      = model.ds  
    dGnd    = ds(3)         # grounded bed
    dFlt    = ds(5)         # floating bed
    dSde    = ds(4)         # sides
    dBed    = dGnd + dFlt   # bed
    
    # solve with corrected velociites :
    model   = self.model
    config  = self.config

    Q       = model.Q
    f_w     = self.f_w
    U_s     = self.U_s
    U_n     = self.U_n
    U_t     = self.U_t

    phi     = TestFunction(Q)
    dtau    = TrialFunction(Q)
            
    u_s     = dot(U_s, U_n)
    v_s     = dot(U_s, U_t)
    U_s     = as_vector([u_s,       v_s      ])
    gradu   = as_vector([u_s.dx(0), u_s.dx(1)])
    gradv   = as_vector([v_s.dx(0), v_s.dx(1)])
    dudn    = dot(gradu, U_n)
    dudt    = dot(gradu, U_t)
    dudz    = u_s.dx(2)
    dvdn    = dot(gradv, U_n)
    dvdt    = dot(gradv, U_t)
    dvdz    = v_s.dx(2)
    gradphi = as_vector([phi.dx(0), phi.dx(1)])
    gradS   = as_vector([S.dx(0),   S.dx(1)  ])
    dphidn  = dot(gradphi, U_n)
    dphidt  = dot(gradphi, U_t)
    dSdn    = dot(gradS,   U_n)
    dSdt    = dot(gradS,   U_t)
    gradphi = as_vector([dphidn, dphidt, phi.dx(2)])
    gradS   = as_vector([dSdn,   dSdt,   S.dx(2)  ])
    
    epi_1  = as_vector([2*dudn + dvdt, 
                        0.5*(dudt + dvdn),
                        0.5*dudz             ])
    epi_2  = as_vector([0.5*(dudt + dvdn),
                             dudn + 2*dvdt,
                        0.5*dvdz             ])
    
    tau_dn_s = phi * rhoi * g * gradS[0] * dx
    tau_dt_s = phi * rhoi * g * gradS[1] * dx
    
    tau_bn_s = - beta**2 * u_s * phi * dBed
    tau_bt_s = - beta**2 * v_s * phi * dBed
    
    tau_pn_s = f_w * N[0] * phi * dSde
    tau_pt_s = f_w * N[1] * phi * dSde
    
    tau_nn_s = - 2 * eta * epi_1[0] * gradphi[0] * dx
    tau_nt_s = - 2 * eta * epi_1[1] * gradphi[1] * dx
    tau_nz_s = - 2 * eta * epi_1[2] * gradphi[2] * dx
    
    tau_tn_s = - 2 * eta * epi_2[0] * gradphi[0] * dx
    tau_tt_s = - 2 * eta * epi_2[1] * gradphi[1] * dx
    tau_tz_s = - 2 * eta * epi_2[2] * gradphi[2] * dx
    
    # mass matrix :
    M = assemble(phi*dtau*dx)
    
    # solution functions :
    tau_dn = Function(Q)
    tau_dt = Function(Q)
    tau_bn = Function(Q)
    tau_bt = Function(Q)
    tau_pn = Function(Q)
    tau_pt = Function(Q)
    tau_nn = Function(Q)
    tau_nt = Function(Q)
    tau_nz = Function(Q)
    tau_tn = Function(Q)
    tau_tt = Function(Q)
    tau_tz = Function(Q)
    
    # solve the linear system :
    solve(M, tau_dn.vector(), assemble(tau_dn_s))
    solve(M, tau_dt.vector(), assemble(tau_dt_s))
    solve(M, tau_bn.vector(), assemble(tau_bn_s))
    solve(M, tau_bt.vector(), assemble(tau_bt_s))
    solve(M, tau_pn.vector(), assemble(tau_pn_s))
    solve(M, tau_pt.vector(), assemble(tau_pt_s))
    solve(M, tau_nn.vector(), assemble(tau_nn_s))
    solve(M, tau_nt.vector(), assemble(tau_nt_s))
    solve(M, tau_nz.vector(), assemble(tau_nz_s))
    solve(M, tau_tn.vector(), assemble(tau_tn_s))
    solve(M, tau_tt.vector(), assemble(tau_tt_s))
    solve(M, tau_tz.vector(), assemble(tau_tz_s))
   
    if config['stokes_balance']['vert_integrate']: 
      s    = "::: vertically integrating '3D-stokes-balance' terms :::"
      print_text(s, self.color())
      
      tau_nn   = model.vert_integrate(tau_nn, Q)
      tau_nt   = model.vert_integrate(tau_nt, Q)
      tau_nz   = model.vert_integrate(tau_nz, Q)
      
      tau_tn   = model.vert_integrate(tau_tn, Q)
      tau_tz   = model.vert_integrate(tau_tz, Q)
      tau_tt   = model.vert_integrate(tau_tt, Q)
      
      tau_dn   = model.vert_integrate(tau_dn, Q)
      tau_dt   = model.vert_integrate(tau_dt, Q)
      
      tau_pn   = model.vert_integrate(tau_pn, Q)
      tau_pt   = model.vert_integrate(tau_pt, Q)
      
      tau_bn   = model.extrude(tau_bn, [3,5], 2, Q)
      tau_bt   = model.extrude(tau_bt, [3,5], 2, Q)

    memb_n   = as_vector([tau_nn, tau_nt, tau_nz])
    memb_t   = as_vector([tau_tn, tau_tt, tau_tz])
    memb_x   = tau_nn + tau_nt + tau_nz
    memb_y   = tau_tn + tau_tt + tau_tz
    membrane = as_vector([memb_x, memb_y, 0.0])
    driving  = as_vector([tau_dn, tau_dt, 0.0])
    basal    = as_vector([tau_bn, tau_bt, 0.0])
    basal_2  = as_vector([tau_nz, tau_tz, 0.0])
    pressure = as_vector([tau_pn, tau_pt, 0.0])
    
    total    = membrane + basal + pressure - driving
    
    # attach the results to the model :
    s    = "::: projecting '3D-stokes-balance' terms onto vector space :::"
    print_text(s, self.color())
    
    model.memb_n   = project(memb_n)
    model.memb_t   = project(memb_t)
    model.membrane = project(membrane)
    model.driving  = project(driving)
    model.basal    = project(basal)
    model.basal_2  = project(basal_2)
    model.pressure = project(pressure)
    model.total    = project(total)



