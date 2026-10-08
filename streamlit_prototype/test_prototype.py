import csv
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import domain
from streamlit.testing.v1 import AppTest


class RulesTest(unittest.TestCase):
    def test_meal_purpose_and_cancel(self):
        samples = domain.make_samples()
        meal = samples[1]
        self.assertTrue(any("목적" in e for e in domain.validate(meal, samples)))
        meal.update(purpose="내부 회의", reviewed=True)
        meal["lines"] = domain.recommend(meal)
        self.assertEqual(meal["lines"][0]["계정과목"], "회의비")
        self.assertEqual(domain.validate(meal, samples), [])
        cancelled = samples[4]
        self.assertEqual(cancelled["lines"][0]["차/대"], "대변")
        self.assertEqual(cancelled["lines"][-1]["차/대"], "차변")

    def test_duplicate_amount_and_incomplete_lines(self):
        samples = domain.make_samples()
        normal = samples[0]
        normal["reviewed"] = True
        self.assertTrue(any("중복" in e for e in domain.validate(normal, samples)))
        normal["duplicate_ok"] = True
        self.assertEqual(domain.validate(normal, samples), [])
        normal["lines"][0]["금액"] = None
        self.assertTrue(domain.validate(normal, samples))
        normal["lines"][0]["금액"] = float("nan")
        self.assertTrue(domain.validate(normal, samples))
        unclear = samples[2]
        self.assertTrue(any("공급가액" in e for e in domain.validate(unclear, samples)))

    def test_storage_and_confirmed_csv(self):
        samples = domain.make_samples()
        r = samples[1]
        r["status"] = "확정 완료"
        r["merchant"] = "=HYPERLINK(123)"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "records.json"
            domain.save(samples, path)
            self.assertEqual(domain.load(path), samples)
        rows = list(csv.reader(io.StringIO(domain.export_csv(samples).decode("utf-8-sig"))))
        self.assertEqual(len(rows), len(r["lines"]) + 1)
        self.assertTrue(rows[1][2].startswith("'="))


class ScreenTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.patcher = patch.object(domain, "DATA_DIR", Path(self.directory.name))
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.app = AppTest.from_file(str(Path(__file__).with_name("app.py")), default_timeout=15).run()

    def assert_clean(self):
        self.assertEqual(list(self.app.exception), [])

    def button(self, label):
        return next(b for b in self.app.button if b.label == label)

    def test_navigation(self):
        self.assert_clean()
        for page in ["영수증함", "검토 작업대", "분개장", "설정 / 데모", "대시보드"]:
            self.app.sidebar.radio[0].set_value(page).run()
            self.assert_clean()

    def test_meal_confirm_export_undo_and_reload(self):
        self.app.sidebar.radio[0].set_value("검토 작업대").run()
        meal_id = self.app.session_state.records[1]["id"]
        self.app.selectbox(key="selected").set_value(meal_id).run()
        self.assertTrue(self.button("검토 완료 · 확정").disabled)
        next(s for s in self.app.selectbox if s.label == "식사 사용 목적").set_value("내부 회의")
        self.button("입력값 저장 · 분개 다시 추천").click().run()
        self.assert_clean()
        next(c for c in self.app.checkbox if c.label == "원본·금액·계정과목을 확인했습니다").check().run()
        self.assertFalse(self.button("검토 완료 · 확정").disabled)
        self.button("검토 완료 · 확정").click().run()
        self.assert_clean()
        saved = domain.load()[1]
        self.assertEqual(saved["status"], "확정 완료")
        self.assertEqual(saved["lines"][0]["계정과목"], "회의비")
        self.app.sidebar.radio[0].set_value("분개장").run()
        self.assert_clean()
        self.assertTrue(self.app.dataframe)
        self.button("검토 화면으로 이동").click().run()
        self.button("확정 취소 · 다시 검토").click().run()
        self.assert_clean()
        self.assertEqual(domain.load()[1]["status"], "검토 필요")
        self.assertEqual(domain.load()[1]["history"][-1]["작업"], "확정 취소")
        restarted = AppTest.from_file(str(Path(__file__).with_name("app.py"))).run()
        self.assertEqual(restarted.session_state.records[1]["history"][-1]["작업"], "확정 취소")

    def test_unclear_correction_and_sample_upload(self):
        self.app.sidebar.radio[0].set_value("검토 작업대").run()
        unclear_id = self.app.session_state.records[2]["id"]
        self.app.selectbox(key="selected").set_value(unclear_id).run()
        next(n for n in self.app.number_input if n.label == "결제금액").set_value(33000)
        self.button("입력값 저장 · 분개 다시 추천").click().run()
        self.assert_clean()
        self.assertEqual(domain.load()[2]["total"], 33000)
        self.assertEqual(domain.load()[2]["original"]["total"], 33000)
        self.app.sidebar.radio[0].set_value("영수증함").run()
        self.button("샘플 분석 체험").click().run()
        self.assert_clean()
        self.assertEqual(len(domain.load()), 7)
        self.assertEqual(self.app.sidebar.radio[0].value, "검토 작업대")


if __name__ == "__main__":
    unittest.main()
