import unittest
from ncode_pipe.engines import DeepLXEngine, get_engine, SmartAutoEngine


class TestDeepLXEngine(unittest.TestCase):
    def test_engine_factory(self):
        eng = get_engine("deeplx")
        self.assertIsInstance(eng, DeepLXEngine)
        self.assertEqual(eng.name, "DeepLX")

    def test_smart_auto_cascade(self):
        # By default prefer_deeplx is False, smart auto uses GoogleFreeNew as primary
        eng = SmartAutoEngine(prefer_deeplx=False)
        self.assertIsNone(eng.deeplx)

        # When prefer_deeplx is True, deepLX is enabled
        eng_deepl = SmartAutoEngine(prefer_deeplx=True)
        self.assertIsNotNone(eng_deepl.deeplx)
        self.assertEqual(eng_deepl.deeplx.name, "DeepLX")


if __name__ == "__main__":
    unittest.main()
