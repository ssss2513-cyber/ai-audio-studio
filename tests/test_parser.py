import unittest
from core.parser import ScriptParser

class TestScriptParser(unittest.TestCase):
    def setUp(self):
        self.parser = ScriptParser(default_speaker="나레이션")

    def test_colon_format(self):
        text = """
        나레이션: 어느 조용한 마을에 철수와 영희가 살고 있었습니다.
        철수: 영희야 안녕!
        영희: 오랜만이야 철수야.
        """
        segments = self.parser.parse(text)
        self.assertEqual(len(segments), 3)
        self.assertEqual(segments[0].speaker, "나레이션")
        self.assertEqual(segments[0].text, "어느 조용한 마을에 철수와 영희가 살고 있었습니다.")
        self.assertEqual(segments[1].speaker, "철수")
        self.assertEqual(segments[1].text, "영희야 안녕!")
        self.assertEqual(segments[2].speaker, "영희")
        self.assertEqual(segments[2].text, "오랜만이야 철수야.")

    def test_bracket_format(self):
        text = """
        [나레이션] 바람이 불어왔다.
        [철수] 춥네.
        [영희]: 따뜻하게 입고 나오지 그랬어.
        """
        segments = self.parser.parse(text)
        self.assertEqual(len(segments), 3)
        self.assertEqual(segments[0].speaker, "나레이션")
        self.assertEqual(segments[0].text, "바람이 불어왔다.")
        self.assertEqual(segments[1].speaker, "철수")
        self.assertEqual(segments[1].text, "춥네.")
        self.assertEqual(segments[2].speaker, "영희")
        self.assertEqual(segments[2].text, "따뜻하게 입고 나오지 그랬어.")

    def test_quotes_and_stage_directions(self):
        text = """
        철수: "오늘 비 올 것 같아."
        영희: (하늘을 보며) 구름이 많긴 하네.
        """
        # 지문 제거 옵션 True
        segments = self.parser.parse(text, remove_stage_directions=True)
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0].text, "오늘 비 올 것 같아.")
        self.assertEqual(segments[1].text, "구름이 많긴 하네.")

    def test_speakers_extraction(self):
        text = """
        나레이션: 시작
        철수: 대사1
        영희: 대사2
        나레이션: 중간
        철수: 대사3
        """
        segments = self.parser.parse(text)
        speakers = self.parser.extract_speakers(segments)
        self.assertEqual(speakers, ["나레이션", "철수", "영희"])

if __name__ == '__main__':
    unittest.main()
