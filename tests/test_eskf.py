"""Analytic/numerical ESKF checks; these do not replace real-hardware GUI acceptance."""
import sys
import unittest
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from eskf import InertialESKF, exp_quaternion, rotation, stationary_imu


class ESKFTests(unittest.TestCase):
    def test_stationary_tilt(self):
        q = exp_quaternion(np.array([.3, -.2, .1]))
        f = InertialESKF(q, [.02, -.01, .03])
        accel = rotation(q).T @ np.array([0, 0, 9.80665])
        for _ in range(500):
            f.predict(accel, [.02, -.01, .03], .02)
            f.update_zero_velocity()
        np.testing.assert_allclose(f.p, 0, atol=1e-10)
        np.testing.assert_allclose(f.v, 0, atol=1e-10)
        self.assertAlmostEqual(np.linalg.norm(f.q), 1)
        self.assertGreater(np.linalg.eigvalsh(f.P).min(), -1e-12)

    def test_constant_acceleration(self):
        f = InertialESKF([1, 0, 0, 0], [0, 0, 0])
        for _ in range(100):
            f.predict([1, 0, 9.80665], [0, 0, 0], .02)
        np.testing.assert_allclose(f.p, [2, 0, 0], atol=1e-10)
        np.testing.assert_allclose(f.v, [2, 0, 0], atol=1e-10)

    def test_rotation_and_bias_correction(self):
        f = InertialESKF([1, 0, 0, 0], [0, 0, 0])
        for _ in range(100):
            f.predict([0, 0, 9.80665], [0, 0, np.pi/4], .02)
        np.testing.assert_allclose(rotation(f.q)@[1, 0, 0], [0, 1, 0], atol=1e-10)
        f = InertialESKF([1, 0, 0, 0], [0, 0, 0])
        for _ in range(500):
            f.predict([0, 0, 9.90665], [0, 0, 0], .02)
            f.update_zero_velocity()
        self.assertLess(abs(f.ba[2]-.1), .01)
        self.assertLess(np.linalg.norm(f.v), .005)
        np.testing.assert_allclose(f.P, f.P.T, atol=1e-12)
        self.assertGreater(np.linalg.eigvalsh(f.P).min(), -1e-12)

    def test_stop_recovers_despite_attitude_error(self):
        # Internal regression: stationary measurements after an erroneous moving state.
        # Old attitude-dependent rest gate rejects this 4.6 degree tilt indefinitely.
        f = InertialESKF(exp_quaternion(np.array([.08, 0., 0.])), [0, 0, 0])
        f.v[0] = .15
        aw = np.tile([0., 0., 9.80665], (20, 1))
        gw = np.zeros((20, 3))
        self.assertGreater(np.linalg.norm(rotation(f.q)@aw[0]+f.gravity), .2)
        self.assertTrue(stationary_imu(aw, gw, f.bg, 9.80665))
        for k in range(100):
            f.predict(aw[0], gw[0], .02)
            f.update_zero_velocity()
            if k % 5 == 0:
                f.update_stationary_imu(aw.mean(0), gw.mean(0))
        self.assertLess(np.linalg.norm(f.v), .001)
        self.assertLess(np.linalg.norm(rotation(f.q)@(aw[0]-f.ba)+f.gravity), .01)
        self.assertGreater(np.linalg.eigvalsh(f.P).min(), -1e-12)

    def test_rest_gate_rejects_rotation_vibration_and_freefall(self):
        aw = np.tile([0., 0., 9.80665], (20, 1))
        gw = np.zeros((20, 3))
        self.assertFalse(stationary_imu(aw, gw + [.1, 0, 0], np.zeros(3), 9.80665))
        noisy = aw.copy()
        noisy[:, 0] = np.tile([-.3, .3], 10)
        self.assertFalse(stationary_imu(noisy, gw, np.zeros(3), 9.80665))
        self.assertFalse(stationary_imu(aw*0, gw, np.zeros(3), 9.80665))
        self.assertFalse(stationary_imu(aw[:10], gw[:10], np.zeros(3), 9.80665))

    def test_history_smoothing_matches_linear_gaussian_solution(self):
        f = InertialESKF([1, 0, 0, 0], [0, 0, 0])
        f.P[:] = 0
        f.P[0, 0] = .01
        f.P[3, 3] = .04
        f.noise[:] = 0
        f.v[0] = .1
        f.record_position(0.)
        for k in range(1, 51):
            f.predict([0, 0, 9.80665], [0, 0, 0], .02)
            f.record_position(k*.02)
        f.update_zero_velocity(sigma=.02)
        expected = .1*f.history_times*(1-.04/(.04+.02**2))
        np.testing.assert_allclose(f.history_positions[:, 0], expected, atol=1e-12)
        np.testing.assert_allclose(f.history_positions[-1], f.p, atol=1e-12)
        self.assertLess(np.ptp(f.history_positions[:, 0]), .001)

    def test_history_does_not_change_filter_and_expires(self):
        a = InertialESKF([1, 0, 0, 0], [0, 0, 0])
        b = InertialESKF([1, 0, 0, 0], [0, 0, 0])
        for k in range(600):
            for f in (a, b):
                f.predict([.01, 0, 9.81], [.002, -.001, .003], .02)
                f.update_zero_velocity()
                if k % 5 == 0:
                    f.update_stationary_imu([.01, 0, 9.81], [.002, -.001, .003])
            a.record_position(k*.02)
        np.testing.assert_allclose(a.p, b.p, atol=1e-12)
        np.testing.assert_allclose(a.P, b.P, atol=1e-12)
        self.assertLessEqual(len(a.history_times), 501)
        self.assertGreaterEqual(a.history_times[0], a.history_times[-1]-10)
        self.assertTrue(np.isfinite(a.history_cross).all())
        with self.assertRaises(ValueError):
            a.record_position(a.history_times[-1])

    def test_invalid_input_does_not_propagate(self):
        f = InertialESKF([1, 0, 0, 0], [0, 0, 0])
        for accel, dt in [([np.nan, 0, 0], .02), ([0, 0, 9.8], 1), ([0, 0, 9.8], 0)]:
            with self.assertRaises(ValueError):
                f.predict(accel, [0, 0, 0], dt)
        np.testing.assert_array_equal(f.p, [0, 0, 0])

if __name__ == '__main__':
    unittest.main()
