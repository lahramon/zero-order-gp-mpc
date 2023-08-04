from casadi import SX, MX, vertcat
from acados_template import AcadosModel
import torch
import gpytorch
import casadi as cas
import matplotlib.pyplot as plt
import numpy as np

timings_names_default = [
    "build_lin_model",
    "query_nodes",
    "get_gp_sensitivities",
    "integrate_acados",
    "integrate_acados_python",
    "integrate_get",
    "integrate_set",
    "set_sensitivities",
    "set_sensitivities_reshape",
    "propagate_covar",
    "get_backoffs",
    "get_backoffs_htj_sig",
    "get_backoffs_htj_sig_matmul",
    "get_backoffs_add",
    "set_tightening",
    "phase_one",
    "check_termination",
    "solve_qp",
    "solve_qp_acados",
    # "total",
]

timings_names_raw = [
    "build_lin_model",
    "query_nodes",
    "get_gp_sensitivities",
    "integrate_acados",
    # "integrate_acados_python",
    "integrate_get",
    "integrate_set",
    "set_sensitivities",
    "set_sensitivities_reshape",
    "propagate_covar",
    # "get_backoffs",
    "get_backoffs_htj_sig",
    "get_backoffs_htj_sig_matmul",
    "get_backoffs_add",
    "set_tightening",
    "phase_one",
    "check_termination",
    # "solve_qp",
    "solve_qp_acados",
]

timings_names_backoffs = [
    "get_backoffs_htj_sig",
    "get_backoffs_htj_sig_matmul",
    "get_backoffs_add"
]

def set_ocp_options(ocp, ocp_opts):
    for key, value in ocp_opts.items():
        if isinstance(value, dict):
            set_ocp_options(getattr(ocp, key), value)
        elif hasattr(ocp, key):
            setattr(ocp, key, value)
            print(f"Set attribute {key} <= {value} of {ocp}")
        else:
            print(f"NOT FOUND: attribute {key} of {ocp}")
    
    return ocp

def export_linear_model(x, u, p):
    nx = x.shape[0]
    nu = u.shape[0]
    np = p.shape[0]

    # linear dynamics for every stage
    A = SX.sym("A",nx,nx)
    B = SX.sym("B",nx,nu)
    # x = SX.sym("x",nx,1)
    # u = SX.sym("x",nu,1)
    w = SX.sym("w",nx,1)
    # sig = SX.sym("w",nx,nx)
    xdot = SX.sym("xdot",nx,1)

    f_expl = A @ x + B @ u + w
    f_impl = xdot - f_expl

    # parameters
    p_lin = vertcat(
        A.reshape((nx**2,1)),
        B.reshape((nx*nu,1)),
        w,
        p
    )

    # acados model
    model = AcadosModel()
    model.disc_dyn_expr = f_expl
    model.f_impl_expr = f_impl
    model.f_expl_expr = f_expl
    model.x = x
    model.xdot = xdot
    model.u = u
    # model.z = z
    model.p = p_lin
    # model.con_h_expr = con_h_expr
    model.name = f"linear_model_with_params_nx{nx}_nu{nu}_np{np}"

    return model

def get_total_timings_per_task(solve_data:list):
    timings_per_task = {}
    for s in solve_data:
        for t_key, t_arr in s.timings.items(): 
            if not any([t_key == key for key in timings_per_task]):
                timings_per_task[t_key] = []
            timings_per_task[t_key] += [np.sum(t_arr)]
    return timings_per_task

def get_total_timings(solve_data:list, timings_names=timings_names_default):
    timings = []
    for s in solve_data:
        t_total = 0.0
        for t_key, t_arr in s.timings.items(): 
            if any([t_key == key for key in timings_names]):
                t_total += np.sum(t_arr)
        timings += [t_total]
    return np.array(timings)

def get_total_iter(solve_data:list):
    n_iter = []
    for s in solve_data:
        n_iter += [s.n_iter]
    return np.array(n_iter)

def plot_timings(solve_data:list, timings_names=timings_names_default):
    # plot timings as bar plot
    fig, ax_bars = plt.subplots()

    n_sim = len(solve_data)

    # x = list(solve_data[0].timings.keys())
    x = np.arange(0.5,len(solve_data)+0.5,1)
    y_old = np.zeros((n_sim,))
    for i, key in enumerate(timings_names):
        y = np.zeros((n_sim,))
        for j,s in enumerate(solve_data):
            y[j] = np.sum(s.timings[key])

        ax_bars.bar(x, y, bottom=y_old)
        if y_old is None:
            y_old = y
        else:
            y_old += y

    n_iter = []
    for j,s in enumerate(solve_data):
        n_iter += [s.n_iter]

    plt.legend(timings_names, bbox_to_anchor=(1.1,1), loc="upper left")

    ax_iter = ax_bars.twinx()
    x_stairs = np.hstack((0.0, x+0.5))
    ax_iter.stairs(n_iter, x_stairs)
    ax_iter.set_ylabel("n iter")

    plt.show()

def sym_mat2vec(mat):
    nx = mat.shape[0]

    if isinstance(mat, np.ndarray):
        i, j = np.triu_indices(nx, m=nx)
    elif isinstance(mat, torch.Tensor):
        i, j = torch.triu_indices(nx, nx)
    else:
        i, j = np.triu_indices(nx, m=nx)

    return mat[i,j]


def vec2sym_mat(vec, nx):

    if isinstance(vec, np.ndarray):
        mat = np.zeros((nx,nx))
        i, j = np.triu_indices(nx, m=nx)
    elif isinstance(vec, torch.Tensor):
        mat = torch.zeros((nx,nx), device=vec.device)
        i, j = torch.triu_indices(nx, nx)
    else:
        mat = SX.zeros(nx,nx)
        i, j = np.triu_indices(nx, m=nx)

    mat[i, j] = vec
    mat.T[i, j] = vec

    return mat

def generate_gp_funs(gp_model, covar_jac=False, B=None):
    if gp_model.train_inputs[0].device.type == "cuda":
        to_tensor = lambda X: torch.Tensor(X).cuda()
        to_numpy = lambda T: T.cpu().numpy()
    else:
        to_tensor = lambda X: torch.Tensor(X)
        to_numpy = lambda T: T.numpy()

    if B is not None:
        B_tensor = to_tensor(B)

    def mean_fun_sum(y):
        with gpytorch.settings.fast_pred_var():
            return gp_model(y).mean.sum(dim=0)

    def covar_fun(y):
        with gpytorch.settings.fast_pred_var():
            return gp_model(y).variance

    def get_mean_dy(y,create_graph=False):
        with gpytorch.settings.fast_pred_var():
            mean_dy = torch.autograd.functional.jacobian(
                mean_fun_sum, 
                y,
                create_graph=create_graph
            )
        return mean_dy

    def gp_sensitivities(y):
        # evaluate GP part (GP jacobians)
        with gpytorch.settings.fast_pred_var():
            y_tensor = torch.autograd.Variable(to_tensor(y), requires_grad=True)
            # DERIVATIVE
            mean_dy = to_numpy(torch.autograd.functional.jacobian(
                mean_fun_sum, 
                y_tensor
            ))

            with torch.no_grad():
                predictions = gp_model(y_tensor) # only model (we want to find true function)
                mean = to_numpy(predictions.mean)
                variance = to_numpy(predictions.variance)

        return mean, mean_dy, variance

    def P_propagation_with_y(y, P_vec, A_nom, create_graph=False):
        variance = covar_fun(y)
        mean_dy = get_mean_dy(y,create_graph=create_graph)
        # P_vec_tensor = to_tensor(P_vec)
        # P_next = P_propagation(P, B @ A_GP, B, torch.diag(variance[0,:]))

        # no diag needed for variance
        nx_nom = B_tensor.shape[0]
        nx_vec = int((nx_nom+1)*nx_nom/2)

        N = y.shape[0]
        P_vec_prop = torch.zeros((N,nx_vec))
        for i in range(N):
            A_GP = mean_dy[:,i,0:nx_nom]
            P = vec2sym_mat(P_vec[i,:], nx_nom)
            A_prop = A_nom[i,:,:] + B_tensor @ A_GP
            P_prop = A_prop @ P @ A_prop.T + B_tensor @ torch.diag(variance[i,:]) @ B_tensor.T
            P_vec_prop[i,:] = sym_mat2vec(P_prop)

        return P_vec_prop

    def gp_sensitivities_with_prop(y,P,A_nom,A_nom_dy):
        # evaluate GP part (GP jacobians)
        with gpytorch.settings.fast_pred_var():
            y_tensor = torch.autograd.Variable(to_tensor(y), requires_grad=True)
            P_tensor = torch.autograd.Variable(to_tensor(P), requires_grad=True)
            A_tensor = torch.autograd.Variable(to_tensor(A_nom), requires_grad=True)
            A_dy_tensor = torch.autograd.Variable(to_tensor(A_nom_dy), requires_grad=False)
            # DERIVATIVE
            mean_dy = to_numpy(torch.autograd.functional.jacobian(
                mean_fun_sum, 
                y_tensor
            ))
            prop_dy_partial = to_numpy(torch.autograd.functional.jacobian(
                lambda y: P_propagation_with_y(y,P_tensor,A_tensor,create_graph=True).sum(dim=0), 
                y_tensor
            ))
            prop_dA = to_numpy(torch.autograd.functional.jacobian(
                lambda A: P_propagation_with_y(y_tensor,P_tensor,A,create_graph=True).sum(dim=0), 
                A_tensor
            ))
            prop_dP = to_numpy(torch.autograd.functional.jacobian(
                lambda P: P_propagation_with_y(y_tensor,P,A_tensor,create_graph=True).sum(dim=0), 
                P_tensor
            ))
            prop_dA_dy = np.transpose(np.diagonal(np.tensordot(prop_dA, A_nom_dy, ([2,3], [1,2])), axis1=1, axis2=2),[0,2,1])
            prop_dy = prop_dy_partial + prop_dA_dy

            with torch.no_grad():
                predictions = gp_model(y_tensor) # only model (we want to find true function)
                mean = to_numpy(predictions.mean)
                prop = to_numpy(P_propagation_with_y(y_tensor,P_tensor,A_tensor))

        return mean, mean_dy, prop, prop_dy, prop_dP
        
    if covar_jac:
        return gp_sensitivities_with_prop
    else:
        return gp_sensitivities

def transform_ocp(ocp):
    nx = ocp.model.x.size()[0]
    nu = ocp.model.u.size()[0]
    nh = ocp.model.con_h_expr.shape[0]

    model_lin = export_linear_model(ocp.model.x, ocp.model.u, ocp.model.p)
    # we replace the nonlinear constraint with linear
    model_lin.con_h_expr = ocp.model.con_h_expr

    ocp.model = model_lin
    ocp.parameter_values = np.zeros((model_lin.p.shape[0],))

    ocp.solver_options.integrator_type = "DISCRETE"
    ocp.solver_options.nlp_solver_type = "SQP_RTI"
    return ocp

def P_propagation(P, A, B, W):
    #  P_i+1 = A P A^T +  B*W*B^T
    return A @ P @ A.T + B @ W @ B.T 

def generate_h_tighten_jac_sig_from_h_tighten(h_tight, x, sig):
    # JIT options
    jit_options = {"flags": ["-O3"], "verbose": True}
    fun_options = {"jit": True, "compiler": "shell", "jit_options": jit_options}

    h_tighten_jac_sig = cas.jacobian(h_tight, sig)
    h_tighten_jac_sig_fun = cas.Function("h_tighten_jac_sig", [x, sig], [h_tighten_jac_sig], fun_options)

    return h_tighten_jac_sig_fun

def propagate(P0, Afun, B, Wfun, y_all, N_sim):
    P_return = [np.zeros(P0.shape)] * (N_sim+1)
    P_return[0] = P0
    for i in range(N_sim):
        y = y_all[i,:]
        P_return[i+1] = P_propagation(P_return[i], Afun(y), B, Wfun(y))
    return P_return
