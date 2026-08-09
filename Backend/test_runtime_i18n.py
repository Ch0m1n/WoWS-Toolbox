from __future__ import annotations

import os
import unittest
from unittest import mock

from runtime_i18n import translate_text


class RuntimeI18nTests(unittest.TestCase):
    def translate(self, value: str) -> str:
        with mock.patch.dict(os.environ, {"WOWS_TOOLBOX_LANGUAGE": "en"}):
            return translate_text(value)

    def test_known_diagnostics_are_meaningful(self) -> None:
        self.assertEqual(
            self.translate("출력 폴더를 만들지 못했어요"),
            "Could not create the output folder",
        )
        self.assertEqual(
            self.translate("파트 원점 데이터 불러오기 실패: 지원하지 않는 형식이에요"),
            "Failed to load part-origin data: unsupported format",
        )
        self.assertEqual(
            self.translate(
                "MODEL LOD 2 메시가 없어요. 최고 품질인 LOD0을 선택해 다시 추출해 주세요."
            ),
            "MODEL LOD 2 has no mesh. Select highest-quality LOD0 and extract again.",
        )

    def test_unknown_message_never_becomes_misleading_fragments(self) -> None:
        translated = self.translate("알 수 없는 새 내부 메시지 ABC_123")
        self.assertNotRegex(translated, r"[가-힣]")
        self.assertIn("translation unavailable", translated)
        self.assertIn("ABC_123", translated)


if __name__ == "__main__":
    unittest.main()
