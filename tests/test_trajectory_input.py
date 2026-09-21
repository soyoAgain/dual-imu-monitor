"""GUI input isolation regression. Synthetic values are not hardware acceptance."""
import sys, unittest
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'gui'))
from app import QApplication, MainWindow, Sample


class TrajectoryInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def test_trajectory_uses_complete_icm_not_mpu_axes(self):
        w = MainWindow()
        try:
            w.calibration_loaded = True
            w.rotation = np.eye(3)
            w.icm_accel_bias = np.zeros(3)
            for k in range(160):
                w.accept_sample(Sample(seq=k, timestamp=k*.02,
                    mpu_accel=(4., 19.613, 1.), mpu_gyro=(100., 0., 0.),
                    icm_accel=(0., 0., 9.80665), icm_gyro=(0., 0., 0.),
                    temperature=25., icm_temperature=25., i2c_errors=0,
                    tx_errors=0, spi_errors=0, sync_errors=0,
                    icm_age_ms=1., saturation_mask=2))
            self.assertIsNotNone(w.eskf)
            np.testing.assert_allclose(w.eskf.v, 0, atol=1e-10)
            np.testing.assert_allclose(w.eskf.p, 0, atol=1e-10)
            np.testing.assert_allclose(w.eskf.bg, 0, atol=1e-10)
            self.assertAlmostEqual(w.last_timestamp, 159*.02+.001)
            self.assertGreater(w.eskf.stationary_updates, 0)
            p = w.eskf.p.copy()
            sample = w.samples[-1]
            sample.timestamp += .02
            sample.icm_gyro = (2000., 0., 0.)
            w.accept_sample(sample)
            np.testing.assert_array_equal(w.eskf.p, p)
            self.assertFalse(w.is_static)
        finally:
            w.close()

if __name__ == '__main__':
    unittest.main()
