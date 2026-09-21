"""15-state inertial ESKF, body-to-world Hamilton quaternion [w,x,y,z].

Right/local attitude errors; error order: position, velocity, angle, ba, bg.
Reference: Joan Sola, https://arxiv.org/abs/1711.02508 . SI units throughout.
Noise defaults are engineering starting values, not measured sensor calibration.
"""
import numpy as np


def skew(v):
    x, y, z = v
    return np.array([[0., -z, y], [z, 0., -x], [-y, x, 0.]])


def multiply(a, b):
    return np.r_[a[0]*b[0] - a[1:]@b[1:],
                 a[0]*b[1:] + b[0]*a[1:] + np.cross(a[1:], b[1:])]


def exp_quaternion(angle):
    theta = np.linalg.norm(angle)
    return np.r_[np.cos(theta/2), np.asarray(angle)*(.5*np.sinc(theta/(2*np.pi)))]


def rotation(q):
    w, x, y, z = q
    return np.array([[1-2*(y*y+z*z), 2*(x*y-w*z), 2*(x*z+w*y)],
                     [2*(x*y+w*z), 1-2*(x*x+z*z), 2*(y*z-w*x)],
                     [2*(x*z-w*y), 2*(y*z+w*x), 1-2*(x*x+y*y)]])


class InertialESKF:
    def __init__(self, quaternion, gyro_bias, gravity=9.80665):
        self.p = np.zeros(3)
        self.v = np.zeros(3)
        self.q = np.asarray(quaternion, dtype=float).copy()
        self.q /= np.linalg.norm(self.q)
        self.ba = np.zeros(3)
        self.bg = np.asarray(gyro_bias, dtype=float).copy()
        self.gravity = np.array([0., 0., -gravity])
        std = np.r_[[.001]*3, [.05]*3, [np.deg2rad(3)]*3, [.15]*3, [.02]*3]
        self.P = np.diag(std**2)
        # Continuous noise densities: accel, gyro, accel-bias RW, gyro-bias RW.
        self.noise = np.diag(np.repeat([.08**2, .008**2, .002**2, .0002**2], 3))
        self.zupt_updates = 0
        self.stationary_updates = 0
        # Fixed-lag position marginals: Cov(past position error, current error).
        self.history_times = np.empty(0)
        self.history_positions = np.empty((0, 3))
        self.history_cross = np.empty((0, 3, 15))

    def predict(self, accel, gyro, dt):
        accel, gyro = np.asarray(accel), np.asarray(gyro)
        if not (np.isfinite(accel).all() and np.isfinite(gyro).all() and 0 < dt <= .1):
            raise ValueError('ESKF requires finite IMU values and 0 < dt <= 0.1 s')
        force, omega = accel-self.ba, gyro-self.bg
        mid_q = multiply(self.q, exp_quaternion(omega*dt/2))
        R = rotation(mid_q)
        world_accel = R@force + self.gravity
        self.p += self.v*dt + .5*world_accel*dt*dt
        self.v += world_accel*dt
        self.q = multiply(self.q, exp_quaternion(omega*dt))
        self.q /= np.linalg.norm(self.q)
        F = np.zeros((15, 15))
        F[0:3, 3:6] = np.eye(3)
        F[3:6, 6:9] = -R@skew(force)
        F[3:6, 9:12] = -R
        F[6:9, 6:9] = -skew(omega)
        F[6:9, 12:15] = -np.eye(3)
        G = np.zeros((15, 12))
        G[3:6, 0:3] = R
        G[6:9, 3:6] = -np.eye(3)
        G[9:12, 6:9] = np.eye(3)
        G[12:15, 9:12] = np.eye(3)
        Phi = np.eye(15) + F*dt + .5*(F@F)*dt*dt
        # Simpson quadrature of propagated continuous noise; PSD by construction.
        Q = G@self.noise@G.T
        half = np.eye(15) + F*dt/2 + (F@F)*dt*dt/8
        Qd = dt/6*(Q + 4*half@Q@half.T + Phi@Q@Phi.T)
        self.history_cross = self.history_cross @ Phi.T
        self.P = Phi@self.P@Phi.T + Qd
        self.P = (self.P+self.P.T)/2

    def update_zero_velocity(self, sigma=.02):
        H = np.zeros((3, 15))
        H[:, 3:6] = np.eye(3)
        V = np.eye(3)*sigma**2
        self._update(-self.v, H, V)
        self.zupt_updates += 1

    def update_stationary_imu(self, accel, gyro):
        """At confirmed rest, observe gravity+ba and zero angular rate+bg.

        Call at <=10 Hz with window means; conservative observation sigmas
        account for correlated samples and unmodelled calibration errors.
        """
        gravity_body = rotation(self.q).T @ (-self.gravity)
        H = np.zeros((6, 15))
        H[:3, 6:9] = skew(gravity_body)
        H[:3, 9:12] = np.eye(3)
        H[3:, 12:15] = np.eye(3)
        residual = np.r_[np.asarray(accel)-gravity_body-self.ba,
                         np.asarray(gyro)-self.bg]
        V = np.diag([.15**2]*3 + [.01**2]*3)
        self._update(residual, H, V)
        self.stationary_updates += 1

    def _update(self, residual, H, V):
        K = np.linalg.solve(H@self.P@H.T + V, H@self.P).T
        dx = K@residual
        # Condition every retained past position on this same observation.
        # This avoids joining old uncorrected points to a corrected endpoint.
        if len(self.history_times):
            C_Ht = self.history_cross @ H.T
            past_gain = np.linalg.solve(H@self.P@H.T + V,
                                        C_Ht.reshape(-1, H.shape[0]).T).T
            past_gain = past_gain.reshape(len(self.history_times), 3, H.shape[0])
            self.history_positions += past_gain @ residual
            self.history_cross -= past_gain @ (H @ self.P)
        A = np.eye(15)-K@H
        self.P = A@self.P@A.T + K@V@K.T  # Joseph form
        self.p += dx[0:3]
        self.v += dx[3:6]
        self.q = multiply(self.q, exp_quaternion(dx[6:9]))
        self.q /= np.linalg.norm(self.q)
        self.ba += dx[9:12]
        self.bg += dx[12:15]
        reset = np.eye(15)
        reset[6:9, 6:9] -= .5*skew(dx[6:9])
        self.history_cross = self.history_cross @ reset.T
        self.P = reset@self.P@reset.T
        self.P = (self.P+self.P.T)/2

    def record_position(self, timestamp, window_seconds=10.):
        """Record the filtered endpoint; future observations smooth this window.

        Additive world position errors need no past attitude reset. Current
        attitude reset is applied to the right side of every cross covariance.
        """
        if not np.isfinite(timestamp):
            raise ValueError("trajectory timestamp must be finite")
        if len(self.history_times) and timestamp <= self.history_times[-1]:
            raise ValueError("trajectory timestamps must increase")
        keep = self.history_times >= timestamp-window_seconds
        self.history_times = np.r_[self.history_times[keep], timestamp]
        self.history_positions = np.concatenate((self.history_positions[keep], self.p[None, :]))
        self.history_cross = np.concatenate((self.history_cross[keep], self.P[None, :3, :]))


def stationary_imu(accel_window, gyro_window, gyro_bias, gravity):
    """Low-dynamic detector independent of estimated attitude/accel bias.

    Cannot distinguish rest from constant-velocity motion; requires ZUPT opt-in.
    """
    aw, gw = np.asarray(accel_window), np.asarray(gyro_window)
    if len(aw) < 20 or len(gw) != len(aw):
        return False
    return bool(np.isfinite(aw).all() and np.isfinite(gw).all()
                and abs(np.linalg.norm(aw.mean(axis=0))-gravity) < .20
                and np.max(aw.std(axis=0)) < .10
                and np.max(np.linalg.norm(gw-gyro_bias, axis=1)) < .06
                and np.max(gw.std(axis=0)) < .015)
