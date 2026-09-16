import tempfile
import unittest
from pathlib import Path

from project_os.astro_profile import AstroSplashEvaluator, AstroSplashImprover
from project_os.database import Database
from project_os.profiles import SHAN_YICHUN_FANSITE
from project_os.workflow import EvolutionAgent


SOURCE = '''<div>
  <img src="/images/封面图1.jpg" class="splash-bg absolute w-full h-full object-contain" data-index="0" />
  <img src="/images/封面图2.jpg" class="splash-bg absolute w-full h-full object-contain opacity-0" data-index="1" />
  <img src="/images/封面图3.jpg" class="splash-bg absolute w-full h-full object-contain opacity-0" data-index="2" />
  <img src="/images/封面图2.jpg" class="splash-bg absolute inset-0 w-full h-full object-cover opacity-0" data-index="1" />
  <img src="/images/封面图3.jpg" class="splash-bg absolute inset-0 w-full h-full object-cover opacity-0" data-index="2" />
</div>'''


class ShanYichunProfileTest(unittest.TestCase):
    def test_preview_preserves_source_and_records_improvement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "BaseLayout.astro"
            source.write_text(SOURCE, encoding="utf-8")
            db = Database(root / "project-os.db")
            db.initialize()
            result = EvolutionAgent(db, AstroSplashEvaluator(), AstroSplashImprover(), SHAN_YICHUN_FANSITE).run(source, dry_run=True)
            self.assertEqual(result["outcome"], "preview")
            self.assertEqual(source.read_text(encoding="utf-8"), SOURCE)
            self.assertEqual(db.history()[0]["decision"], "preview")
            self.assertGreater(AstroSplashEvaluator().evaluate(AstroSplashImprover().improve(SOURCE, AstroSplashEvaluator().evaluate(SOURCE).evidence).content).overall_score, AstroSplashEvaluator().evaluate(SOURCE).overall_score)
