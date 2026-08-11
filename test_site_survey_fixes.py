#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_site_survey_fixes.py

ชุดทดสอบสำหรับบั๊กที่แก้ไปทั้งหมด (Site Survey Generator)

รันด้วย:
    python -m unittest test_site_survey_fixes -v

ครอบคลุม:
  1. quotation_processor      - รหัส Code AD1/SD1/ADD1 ต้องถูกอ่านเป็น product
  2. quotation_processor      - W02.1F1 ต้องไม่ถูกตัดเหลือ W02
  3. quotation_processor      - ไม่พบสินค้า ต้องมี warning ไม่ใช่เงียบๆ
  4. site_survey_generator    - ตัวอักษรไทยซ้ำที่ถูกต้อง (รวมมุ้งแล้ว) ต้องไม่ถูกยุบ
  5. window_door_image_gen    - ป้าย W/H ต้องอยู่กึ่งกลางลูกศร
  6. server.py                - ชื่อไฟล์รูป AD1.jpg ต้อง map เป็น ref AD1
  7. Generate.html            - regex ฝั่ง client ต้อง match AD1 เช่นกัน
  8. End-to-end               - PDF จริง -> เอกสารมีรูปแบบ + ข้อความถูกต้อง
"""

import os
import re
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
os.chdir(BASE_DIR)
sys.path.insert(0, str(BASE_DIR))

SAMPLE_PDF = Path("/Users/ken/Downloads/คุณภาธร-Quo2026070804.pdf")

from quotation_processor import EnhancedTOSTEMQuotationProcessor  # noqa: E402
from site_survey_generator import collapse_doubled_thai  # noqa: E402


# ---------------------------------------------------------------- helpers
def server_ref_from_filename(filename):
    """สำเนา logic จาก server.py extract_ref_from_filename

    (ฟังก์ชันจริงถูกนิยามซ้อนอยู่ใน route จึง import ตรงๆ ไม่ได้
     เทสนี้จึงตรวจ logic คู่กับ test_server_regex_matches_source ที่กันไม่ให้ทั้งสองหลุดจากกัน)
    """
    name = os.path.splitext(filename)[0]
    if re.match(r'^(DSC|DCIM|PXL|PANO|VID|MOV|SCR|IMG)[\d_\-]', name, re.IGNORECASE):
        return None
    REF = r'[A-Z]{1,3}\d+(?:\.\d+)?(?:[FT]\d*)?'
    L, R = r'(?<![A-Za-z0-9])', r'(?![A-Za-z0-9])'
    for p in (rf'^({REF}){R}', rf'{L}({REF})$', rf'[_\-\s]({REF})[_\-\s]', rf'{L}({REF}){R}'):
        m = re.search(p, name, re.IGNORECASE)
        if m:
            return m.group(1).upper()
    return None


# ---------------------------------------------------------------- 1 & 2
class TestRefCodeParsing(unittest.TestCase):
    """รหัส Code ที่ไม่ได้ขึ้นต้นด้วย D/W ต้องถูกอ่านได้"""

    def setUp(self):
        self.p = EnhancedTOSTEMQuotationProcessor()

    def test_ad1_is_recognised(self):
        """บั๊กต้นเรื่อง: AD1 ไม่ถูกมองเป็น product line -> ไม่มีรูปแบบเลย"""
        line = ('AD1 WE-70 Airflow door L/R (Knock-down) รวมมุ้งแล้ว+กระจกเขียวตัดแสง '
                '885 2190 1 27,700.00 27,700.00')
        self.assertTrue(self.p._is_main_product_line(line))
        self.assertEqual(self.p._extract_ref_from_line(line), 'AD1')

    def test_other_prefixes(self):
        for line, want in [
            ('AD2 ATIS Airflow door 900 2100 1 1.00', 'AD2'),
            ('AD1F WE-70 Fixed 500 600 1 1.00', 'AD1F'),
            ('AD1T1 WE-70 Transom 500 300 1 1.00', 'AD1T1'),
            ('SD1 WE-70 Sliding door 2000 2200 1 1.00', 'SD1'),
            ('ADD1 GRANT Airflow 800 900 1 1.00', 'ADD1'),
        ]:
            with self.subTest(line=line):
                self.assertTrue(self.p._is_main_product_line(line))
                self.assertEqual(self.p._extract_ref_from_line(line), want)

    def test_existing_codes_still_work(self):
        """กันการ regress ของรหัสเดิม"""
        for line, want in [
            ('W1 WE-70 Casement window 1000 1200 1 1.00', 'W1'),
            ('D1 WE-70 Sliding door 2000 2200 1 1.00', 'D1'),
            ('W6 GRANT Fix window 800 900 1 1.00', 'W6'),
            ('W11.1 GRANT Fix 800 900 1 1.00', 'W11.1'),
            ('D1T1 ATIS Transom 500 300 1 1.00', 'D1T1'),
            ('W6F1 GRANT Fixed 500 600 1 1.00', 'W6F1'),
            ('3/16 WE-70 Casement 900 1200 1 1.00', '3/16'),
        ]:
            with self.subTest(line=line):
                self.assertTrue(self.p._is_main_product_line(line))
                self.assertEqual(self.p._extract_ref_from_line(line), want)

    def test_fixed_panel_ref_not_truncated(self):
        """บั๊กเดิม: W02.1F1 และ W02.1F2 ถูกตัดเหลือ W02 ทั้งคู่ -> รวมร่างผิด"""
        self.assertEqual(
            self.p._extract_ref_from_line('W02.1F1 WE-70 Fixed 500 600 1 1.00'), 'W02.1F1')
        self.assertEqual(
            self.p._extract_ref_from_line('W02.1F2 WE-70 Fixed 500 600 1 1.00'), 'W02.1F2')
        self.assertEqual(
            self.p._extract_ref_from_line('D1.5F1 ATIS Fixed 500 600 1 1.00'), 'D1.5F1')

    def test_non_product_lines_rejected(self):
        """ที่อยู่/หมายเหตุ/หัวตาราง ต้องไม่ถูกมองเป็นสินค้า"""
        for line in [
            '1104/314 Phatthanakan, Suan Luang, Bangkok, 10250',
            '063-720-5750; kunnapabtostem@hotmail.com',
            '- ราคาประเมินค่าขนส่งคิดจากโรงงาน tostem นวนคร',
            'Kunnapab Home Solution Ltd. (Head Office)',
            'Code Series Description Size (mm.) Qty. Price/Unit',
            'Attn.  คุณภาธร',
            'รวมทั้งหมด 1 รายการ',
            'ราคารวมสุทธิ 31,376.00',
            'Project  บ้านคุณแม่คุณไบร์ท',
        ]:
            with self.subTest(line=line):
                self.assertFalse(self.p._is_main_product_line(line))


# ---------------------------------------------------------------- 4
class TestThaiDoubledLetters(unittest.TestCase):
    """คำไทยที่มีตัวซ้ำจริง ต้องไม่ถูกยุบ (บั๊ก รวมมุ้งแล้ว -> รวมุ้งแล้ว)"""

    def test_legit_thai_preserved(self):
        for s in ['รวมมุ้งแล้ว', 'Airflow door L/R รวมมุ้งแล้ว', 'ธรรมดา',
                  'บรรจุ', 'กรรไกร', 'พรรณ', 'กระจกเขียวตัดแสง 6mm',
                  'ประตูบานเลื่อนธรรมดา']:
            with self.subTest(s=s):
                self.assertEqual(collapse_doubled_thai(s), s)

    def test_pdf_artifact_still_cleaned(self):
        """ข้อความซ้อนจาก PDF ต้องยังถูกยุบเหมือนเดิม"""
        for s in ['สส่ว่วนนลลดดคค่า่าสสินินคค้า้า',
                  'รราาคคาารรววมมสสุทุทธธิิ',
                  'รราาคคาารรววมมสส่ว่วนนลลดด']:
            with self.subTest(s=s):
                out = collapse_doubled_thai(s)
                self.assertNotEqual(out, s, 'artifact ควรถูกยุบ')
                self.assertLess(len(out), len(s))

    def test_empty_and_none_safe(self):
        self.assertEqual(collapse_doubled_thai(''), '')
        self.assertIsNone(collapse_doubled_thai(None))


# ---------------------------------------------------------------- 5
class TestDimensionLabelCentering(unittest.TestCase):
    """ป้าย W/H ต้องอยู่กึ่งกลางลูกศร (เดิมเยื้อง 30px และ 50px)"""

    TOL = 2.0  # px

    @classmethod
    def setUpClass(cls):
        import numpy as np
        from PIL import Image
        from window_door_image_generator import WindowDoorImageGenerator
        cls.np, cls.Image = np, Image
        cls.gen = WindowDoorImageGenerator()

    def _measure(self, base_png, w, h, desc):
        np, Image = self.np, self.Image
        out = self.gen._create_annotated_image(Path(base_png), w, h, 'UNITTEST', desc)
        self.assertIsNotNone(out, 'ต้องสร้างรูปได้')
        dark = np.array(Image.open(out).convert('L')) < 128

        multi = bool(re.search(r'\((\d+)\)', desc))
        margin_top = 120 if multi else 80
        arrow_row = margin_top - 30           # ลูกศร W (แถวล่างสุดของกลุ่ม)
        arrow_col = 80 - 30                   # ลูกศร H

        xs = np.where(dark[arrow_row])[0]
        w_arrow_c = (xs.min() + xs.max()) / 2
        band = dark[arrow_row - 24:arrow_row - 3, :]
        tx = np.where(band.any(axis=0))[0]
        w_text_c = (tx.min() + tx.max()) / 2

        ys = np.where(dark[:, arrow_col])[0]
        h_arrow_c = (ys.min() + ys.max()) / 2
        left = dark[:, :arrow_col - 6]
        ty = np.where(left.any(axis=1))[0]
        h_text_c = (ty.min() + ty.max()) / 2

        try:
            os.remove(out)
        except OSError:
            pass
        return w_text_c - w_arrow_c, h_text_c - h_arrow_c

    def test_single_panel_labels_centred(self):
        dw, dh = self._measure('TOSTEM Drawing/airflow-L.png', 885, 2190, 'Airflow door')
        self.assertLessEqual(abs(dw), self.TOL, f'ป้าย W เยื้อง {dw:+.1f}px')
        self.assertLessEqual(abs(dh), self.TOL, f'ป้าย H เยื้อง {dh:+.1f}px')

    def test_multi_panel_labels_centred(self):
        dw, dh = self._measure('TOSTEM Drawing/3-panels-sliding.png', 2700, 2200,
                               'Sliding door (3)')
        self.assertLessEqual(abs(dw), self.TOL, f'ป้าย W เยื้อง {dw:+.1f}px')
        self.assertLessEqual(abs(dh), self.TOL, f'ป้าย H เยื้อง {dh:+.1f}px')


# ---------------------------------------------------------------- 6
class TestPhotoFilenameToRef(unittest.TestCase):
    """ชื่อไฟล์รูป -> ref (เดิม AD1.jpg กลายเป็น D1 เงียบๆ)"""

    def test_maps_correctly(self):
        for fn, want in [
            ('AD1.jpg', 'AD1'), ('AD1_front.jpg', 'AD1'), ('AD1-2.jpg', 'AD1'),
            ('AD1 front.jpg', 'AD1'), ('AD2.png', 'AD2'), ('ADD1.jpg', 'ADD1'),
            ('SD1.jpg', 'SD1'), ('D1.jpg', 'D1'), ('W2.jpg', 'W2'), ('w2.jpg', 'W2'),
            ('site-D1-photo.jpg', 'D1'), ('W02.1F1.png', 'W02.1F1'),
        ]:
            with self.subTest(fn=fn):
                self.assertEqual(server_ref_from_filename(fn), want)

    def test_camera_names_unmatched(self):
        """ชื่อไฟล์กล้อง ต้องไม่ถูกเดาเป็น ref มั่วๆ"""
        for fn in ['IMG_1234.jpg', 'photo1.jpg', 'DSC00123.jpg', 'PXL_20260811.jpg']:
            with self.subTest(fn=fn):
                self.assertIsNone(server_ref_from_filename(fn))

    def test_server_regex_matches_source(self):
        """กันไม่ให้ logic ใน server.py กับสำเนาในเทสหลุดจากกัน"""
        src = Path('server.py').read_text(encoding='utf-8')
        self.assertIn(r"REF = r'[A-Z]{1,3}\d+(?:\.\d+)?(?:[FT]\d*)?'", src)
        self.assertIn("DSC|DCIM|PXL|PANO|VID|MOV|SCR|IMG", src)
        self.assertNotIn(r"r'^([DW][A-Z]?\d+(?:\.\d+)?)'", src,
                         'server.py ยังมี pattern เดิมที่จำกัดแค่ D/W')


# ---------------------------------------------------------------- 7
class TestFrontendRefRegex(unittest.TestCase):
    """regex ฝั่ง client ใน Generate.html ต้อง match AD1 เช่นกัน"""

    def test_generate_html_updated(self):
        html = Path('Generate.html').read_text(encoding='utf-8')
        self.assertIn("const REF = '[A-Z]{1,3}", html)
        self.assertNotIn("/^([DW][A-Z]?\\d+(?:\\.\\d+)?)\\b/i", html,
                         'Generate.html ยังมี pattern เดิมที่จำกัดแค่ D/W')

    def test_photo_drop_area_present(self):
        """กล่องอัปโหลดรูปภาพแบบลากวาง"""
        html = Path('Generate.html').read_text(encoding='utf-8')
        self.assertIn('id="photoUploadArea"', html)
        self.assertIn('handlePhotoDrop', html)
        self.assertIn('.photo-upload-area.dragover', html)

    @unittest.skipIf(subprocess.call(['which', 'node'],
                                     stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL) != 0,
                     'ไม่มี node')
    def test_regex_behaviour_in_node(self):
        js = r"""
        const REF = '[A-Z]{1,3}\\d+(?:\\.\\d+)?(?:[FT]\\d*)?';
        const pats = [
            new RegExp(`^(${REF})(?![A-Z0-9])`, 'i'),
            new RegExp(`(?:^|[^A-Z0-9])(${REF})$`, 'i'),
            new RegExp(`[_\\-\\s](${REF})[_\\-\\s]`, 'i'),
            new RegExp(`(?:^|[^A-Z0-9])(${REF})(?![A-Z0-9])`, 'i')
        ];
        function find(name, refs) {
            for (const p of pats) {
                const m = name.match(p);
                if (m && refs.includes(m[1].toUpperCase())) return m[1].toUpperCase();
            }
            return null;
        }
        const cases = [
            ['AD1', ['AD1'], 'AD1'], ['AD1_front', ['AD1'], 'AD1'],
            ['ADD1', ['ADD1'], 'ADD1'], ['SD1', ['SD1'], 'SD1'],
            ['D1', ['D1','AD1'], 'D1'], ['w2', ['W2'], 'W2'],
            ['IMG_1234', ['AD1'], null], ['photo1', ['AD1'], null],
            ['AD1', ['D1'], null]
        ];
        let bad = 0;
        for (const [n, refs, want] of cases) if (find(n, refs) !== want) bad++;
        console.log(bad === 0 ? 'OK' : 'FAIL:' + bad);
        """
        out = subprocess.run(['node', '-e', js], capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), 'OK', out.stdout + out.stderr)


# ---------------------------------------------------------------- 3 & 8
@unittest.skipUnless(SAMPLE_PDF.exists(), f'ไม่มีไฟล์ตัวอย่าง {SAMPLE_PDF}')
class TestEndToEnd(unittest.TestCase):
    """PDF จริง -> ข้อมูล -> เอกสาร"""

    @classmethod
    def setUpClass(cls):
        from site_survey_generator import (
            enhanced_process_quotation_file_with_smart_mosquito as proc)
        cls.result = proc(str(SAMPLE_PDF))
        cls.products = cls.result.get('data', {}).get('products', [])

    def test_product_extracted(self):
        self.assertEqual(len(self.products), 1, 'ต้องได้สินค้า 1 รายการ')

    def test_product_fields(self):
        p = self.products[0]
        self.assertEqual(p['ref'], 'AD1')
        self.assertEqual(p['series'], 'WE-70')
        self.assertEqual(p['width'], 885)
        self.assertEqual(p['height'], 2190)
        self.assertEqual(p['qty'], 1)
        self.assertEqual(p['insect_screen'], 'Yes')
        self.assertIn('กระจกเขียวตัดแสง', p['glass'])

    def test_thai_text_not_corrupted(self):
        """ม สองตัวต้องอยู่ครบ"""
        self.assertIn('รวมมุ้งแล้ว', self.products[0]['product_type'])

    def test_drawing_matched(self):
        from window_door_image_generator import WindowDoorImageGenerator
        g = WindowDoorImageGenerator()
        p = self.products[0]
        img = g.match_product_type_to_image(p['product_type'], p['ref'], p.get('Type2', ''))
        self.assertIsNotNone(img, 'ต้องหารูปแบบประตูเจอ')
        self.assertIn('airflow', img.name.lower())

    def test_no_products_produces_warning(self):
        """ไฟล์ที่ไม่มีสินค้า ต้องเตือน ไม่ใช่ success เงียบๆ"""
        other = Path('Sash-2026-02-2420-3821538_20543770-ELE.pdf')
        if not other.exists():
            self.skipTest('ไม่มีไฟล์ทดสอบ')
        p = EnhancedTOSTEMQuotationProcessor()
        r = p.process_tostem_quotation_pdf(str(other))
        self.assertEqual(len(r['data']['products']), 0)
        self.assertIn('warning', r, 'ต้องมี warning เมื่อไม่พบสินค้า')
        self.assertIn('Code', r['warning'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
