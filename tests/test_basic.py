import unittest

import cronphase


class PackageTest(unittest.TestCase):
    def test_public_api_exports(self):
        for name in cronphase.__all__:
            self.assertTrue(hasattr(cronphase, name), name)

    def test_version_is_set(self):
        self.assertTrue(cronphase.__version__)


if __name__ == "__main__":
    unittest.main()
