# use of main5.py

from flask import Flask, render_template, request, jsonify, send_file
import pdfplumber
import pandas as pd
import re
import io
from datetime import datetime
import os
from werkzeug.utils import secure_filename
from typing import List, Dict, Optional

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
app.config['UPLOAD_FOLDER'] = 'uploads'

class _NoopLogger:
    def info(self, *args, **kwargs): pass
    def error(self, *args, **kwargs): pass
    def warning(self, *args, **kwargs): pass

import logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

class InsectScreenComparator:
    """Enhanced Insect Screen Comparator with FIXED detection logic"""
    
    def __init__(self):
        self.screen_indicators = [
            'insect screen', 'screen', 'มุ้ง', 'มุ้งลวด',
            'bdf', 'bdn', 'mesh', 'net', 'screen awning', 'msh'
        ]
    
    def compare_insect_screens(self, site_data: List[Dict], ele_data: List[Dict]) -> List[Dict]:
        """เปรียบเทียบข้อมูลมุ้งระหว่าง Site Survey และ ELE"""
        results = []
        
        logger.info(f"Starting comparison: Site={len(site_data)}, ELE={len(ele_data)}")
        
        # สร้าง ELE lookup สำหรับ screen detection
        ele_screen_lookup = self._build_ele_screen_lookup(ele_data)
        
        for site_item in site_data:
            ref = site_item.get("Ref", "")
            site_insect_screen = site_item.get("Insect screen", "No")
            
            # Normalize site screen requirement
            site_has_screen = self._normalize_screen_requirement(site_insect_screen)
            
            # Check ELE for screen items
            ele_screen_items = self._find_ele_screen_items(ref, ele_screen_lookup, ele_data)
            ele_has_screen = len(ele_screen_items) > 0
            
            logger.info(f"{ref}: Site={site_has_screen}, ELE={ele_has_screen}, ELE items={len(ele_screen_items)}")
            
            # Create result
            result = self._create_comparison_result(
                ref, site_has_screen, ele_has_screen, ele_screen_items, site_item
            )
            results.append(result)
        
        return results
    
    def _build_ele_screen_lookup(self, ele_data: List[Dict]) -> Dict[str, List[Dict]]:
        """สร้าง lookup table สำหรับ ELE screen items"""
        ele_screen_lookup = {}
        
        logger.info("=== Building ELE Screen Lookup (FIXED VERSION + GIESTA EXCLUSION) ===")
        
        for item in ele_data:
            ref = item.get("Ref", "")
            element_type = item.get("Element_Type", "")
            description = item.get("Description", "")
            
            logger.info(f"Analyzing {ref}: Element_Type='{element_type}', Description='{description}'")
            
            # GIESTA EXCLUSION - Skip GIESTA products completely
            combined_text = f"{element_type} {description}".upper()
            if "GIESTA" in combined_text:
                logger.info(f"GIESTA EXCLUSION: {ref} - GIESTA product excluded from screen lookup")
                continue
            
            # Also check all item values for GIESTA
            is_giesta = False
            for key, value in item.items():
                if value and "GIESTA" in str(value).upper():
                    logger.info(f"GIESTA EXCLUSION: {ref} - GIESTA found in {key}: {value}")
                    is_giesta = True
                    break
            
            if is_giesta:
                continue
            
            # FIXED screen detection (only if not GIESTA)
            is_screen_item = self._is_screen_item(item)
            
            if is_screen_item:
                if ref not in ele_screen_lookup:
                    ele_screen_lookup[ref] = []
                ele_screen_lookup[ref].append(item)
                
                logger.info(f"SCREEN: {ref} added as screen item - {element_type}")
            else:
                logger.info(f"NOT SCREEN: {ref} - {element_type}")
        
        logger.info(f"=== Screen Lookup Summary: {len(ele_screen_lookup)} references have screens ===")
        for ref, items in ele_screen_lookup.items():
            logger.info(f"  {ref}: {len(items)} screen items")
            for item in items:
                logger.info(f"    - {item.get('Element_Type', 'Unknown')}")
        
        return ele_screen_lookup
    
    def _is_screen_item(self, item: Dict) -> bool:
        """Enhanced screen detection with proper GIESTA handling"""
        
        ref = item.get("Ref", "")
        element_type = item.get("Element_Type", "")
        description = item.get("Description", "")
        
        logger.info(f"Enhanced screen detection for {ref} - {element_type} - {description}")
        
        # Check explicit flags first
        if item.get("Is_GIESTA_Product", False):
            logger.info(f"GIESTA FLAG: {ref} is GIESTA product - NOT a screen")
            return False
        
        if item.get("Is_Screen_Product", None) is True:
            logger.info(f"SCREEN FLAG: {ref} is explicitly marked as screen")
            return True
        
        if item.get("Is_Screen_Product", None) is False:
            logger.info(f"NOT SCREEN FLAG: {ref} is explicitly marked as NOT screen")
            return False
        
        # Text-based GIESTA detection (backup)
        combined_text = f"{element_type} {description}".upper()
        if "GIESTA" in combined_text:
            logger.info(f"GIESTA TEXT: {ref} contains GIESTA - NOT a screen")
            return False
        
        # Screen detection for INSECT SCREEN products
        if "INSECT SCREEN FOR SASH PRODUCT" in description.upper():
            logger.info(f"EXPLICIT INSECT SCREEN: {ref}")
            return True
        
        # Pure screen codes
        if any(code in element_type.upper() for code in ['MSH', 'MESH', 'SCREEN']):
            logger.info(f"PURE SCREEN CODE: {element_type} for {ref}")
            return True
        
        # Screen-prefixed mixed codes (from insect screen pages)
        if element_type.startswith("SCREEN_"):
            logger.info(f"SCREEN PREFIX: {element_type} for {ref}")
            return True
        
        # Known window/door codes - NOT screens
        window_door_codes = ['ECA', 'EF1', 'EM2', 'EVV', 'FFA', 'WD6', 'ET2', 'EP3']
        if any(code in element_type.upper() for code in window_door_codes):
            logger.info(f"WINDOW/DOOR CODE: {element_type} for {ref} - NOT screen")
            return False
        
        # Mixed codes - need glass verification
        mixed_codes = ['BDF', 'BDN', 'BA7', 'BXC']
        if any(code in element_type.upper() for code in mixed_codes):
            has_glass = self._has_glass_data(item)
            if has_glass:
                logger.info(f"MIXED CODE WITH GLASS: {element_type} for {ref} - NOT screen")
                return False
            else:
                logger.info(f"MIXED CODE WITHOUT GLASS: {element_type} for {ref} - LIKELY screen")
                return True
        
        logger.info(f"NO MATCH: {ref} - {element_type} - NOT a screen")
        return False
    
    def _validate_reference_format(self, ref: str) -> bool:
        """ตรวจสอบรูปแบบ reference ว่าถูกต้องหรือไม่ - แก้ไขแล้ว"""
        if not ref:
            return False
        
        # Skip invalid references
        invalid_refs = [
            "Attention", "W", "WE70", "Airflow", "Wo", "Ho", "Description",
            "Reference", "Code", "Product", "Type", "Survey", "Page"
        ]
        
        if ref in invalid_refs:
            return False
        
        valid_patterns = [
            r'^[WDA]A?\d+$',          # W1, W11, D1, D11, A1, WA1, DA1
            r'^[WDA]A?\d+\.\d+$',     # W1.1, W1.2, D1.1, A1.1, WA1.1, DA1.1
            r'^ADD$'                  # Special case
        ]
        
        for pattern in valid_patterns:
            if re.match(pattern, ref):
                return True
        
        return False

    def _has_glass_data(self, item: Dict) -> bool:
        """Enhanced glass detection - more accurate"""
        glass_found = False
        
        logger.info(f"   === Enhanced Glass Detection for {item.get('Ref', 'Unknown')} ===")
        
        # Check all keys in the item
        for key, value in item.items():
            if not value or not str(value).strip():
                continue
                
            key_upper = str(key).upper()
            value_str = str(value).strip()
            
            logger.info(f"   Checking key: '{key}' = '{value}'")
            
            # Enhanced glass-related patterns
            glass_patterns = [
                'GLASS', 'GW', 'GH', 'GLS',                    # Basic patterns
                'GLASS_1', 'GLASS_2', 'GLASS_3',               # Table patterns
                'GLASS #1', 'GLASS #2', 'GLASS #3',            # PDF patterns
                'GLAZING', 'PANE', 'IGU',                      # Additional glass terms
                'THICKNESS', 'THK'                             # Glass thickness indicators
            ]
            
            # Check for glass-related keys
            is_glass_key = any(pattern in key_upper for pattern in glass_patterns)
            
            # Also check for numbered glass columns
            is_numbered_glass = (key_upper.startswith('GLASS') and any(c.isdigit() for c in key_upper))
            
            if is_glass_key or is_numbered_glass:
                logger.info(f"   Found glass-related key: {key}")
                
                # Check if the value is meaningful (not empty/zero)
                if value_str and value_str != '0' and value_str.lower() not in ['none', 'null', '', '-', 'n/a']:
                    # For numeric values, check if greater than 0
                    if value_str.replace('.', '').replace('-', '').isdigit():
                        try:
                            num_value = float(value_str)
                            if num_value > 0:
                                glass_found = True
                                logger.info(f"   ✅ MEANINGFUL GLASS DATA: {key}={value} (numeric > 0)")
                                break
                        except ValueError:
                            continue
                    else:
                        # Non-numeric but meaningful glass data
                        if len(value_str) > 1:  # Not just a single character
                            glass_found = True
                            logger.info(f"   ✅ MEANINGFUL GLASS DATA: {key}={value} (text)")
                            break
                else:
                    logger.info(f"   ❌ Empty/zero glass value: {key}={value}")
        
        # Additional check: Look for dimension columns that could be glass
        # This helps catch glass data in unnamed columns
        dimension_count = 0
        meaningful_dimensions = []
        
        for key, value in item.items():
            if not value:
                continue
                
            value_str = str(value).strip()
            if value_str.replace('.', '').isdigit():
                try:
                    num_value = float(value_str)
                    # Typical glass dimensions or thickness
                    if 3 <= num_value <= 50:  # Glass thickness range (mm)
                        meaningful_dimensions.append((key, num_value, 'thickness'))
                    elif 200 <= num_value <= 8000:  # Glass dimension range (mm)
                        meaningful_dimensions.append((key, num_value, 'dimension'))
                    dimension_count += 1
                except ValueError:
                    continue
        
        # If we have multiple dimensions and some could be glass, be cautious
        if dimension_count >= 4 and not glass_found:
            logger.info(f"   ⚠️  Multiple dimensions found ({dimension_count}) - checking patterns")
            # Look for thickness-like values that could indicate glass
            thickness_values = [d for d in meaningful_dimensions if d[2] == 'thickness']
            if len(thickness_values) >= 2:
                logger.info(f"   ⚠️  Multiple thickness values found - likely has glass specifications")
                glass_found = True
        
        logger.info(f"   === FINAL GLASS RESULT for {item.get('Ref', 'Unknown')}: {glass_found} ===")
        return glass_found

    def _has_width_and_height(self, item: Dict) -> bool:
        """Check for width and height dimensions - more accurate detection"""
        width_values = []
        height_values = []
        potential_dimensions = []
        
        logger.info(f"   === Dimension Detection for {item.get('Ref', 'Unknown')} ===")
        
        # Check all keys for dimension data
        for key, value in item.items():
            if not value or not str(value).strip():
                continue
                
            key_upper = str(key).upper()
            value_str = str(value).strip()
            
            # Check if it's a number in reasonable dimension range
            if value_str.replace('.', '').replace('-', '').isdigit():
                try:
                    num_value = float(value_str)
                    if 200 <= num_value <= 8000:  # Reasonable window/screen dimensions (mm)
                        
                        # Width patterns
                        width_patterns = ['WIDTH', 'WO', 'W=', 'WI', 'W_', 'PROD_WIDTH', 'W1', 'W2']
                        if any(pattern in key_upper for pattern in width_patterns):
                            width_values.append((key, num_value))
                            logger.info(f"   Width found: {key}={value}")
                        
                        # Height patterns  
                        height_patterns = ['HEIGHT', 'HO', 'H=', 'HE', 'H_', 'PROD_HEIGHT', 'H1', 'H2']
                        if any(pattern in key_upper for pattern in height_patterns):
                            height_values.append((key, num_value))
                            logger.info(f"   Height found: {key}={value}")
                        
                        # Store as potential dimension
                        potential_dimensions.append((key, num_value))
                        
                except ValueError:
                    continue
        
        has_width = len(width_values) > 0
        has_height = len(height_values) > 0
        
        # If explicit width/height not found, try to infer from potential dimensions
        if not (has_width and has_height) and len(potential_dimensions) >= 2:
            logger.info(f"   No explicit W/H found, but {len(potential_dimensions)} potential dimensions")
            # Sort by value to try to identify width vs height
            potential_dimensions.sort(key=lambda x: x[1])
            
            # Assume we have at least width and height if we have 2+ reasonable dimensions
            if len(potential_dimensions) >= 2:
                has_width = True
                has_height = True
                logger.info(f"   Inferred W/H from dimensions: {potential_dimensions[:2]}")
        
        result = has_width and has_height
        logger.info(f"   === DIMENSION RESULT: has_width={has_width}, has_height={has_height}, result={result} ===")
        return result
    
    def _find_ele_screen_items(self, ref: str, ele_screen_lookup: Dict, all_ele_data: List[Dict]) -> List[Dict]:
        """หา ELE screen items แบบละเอียด - รองรับ parent-child matching"""
        
        logger.info(f"Looking for screens for {ref}")
        
        # 1. Direct match
        ele_screen_items = ele_screen_lookup.get(ref, [])
        
        if ele_screen_items:
            logger.info(f"Direct match for {ref}: {len(ele_screen_items)} items")
            return ele_screen_items
        
        # 4. Partial reference matching (เหมือนเดิม)
        for ele_ref in ele_screen_lookup.keys():
            if self._is_reference_match(ref, ele_ref):
                logger.info(f"Partial match: {ref} -> {ele_ref}")
                return ele_screen_lookup[ele_ref]
        
        # 5. Look in all data with broader matching
        logger.info(f"Searching all ELE data for potential screens for {ref}")
        potential_screens = []
        
        for item in all_ele_data:
            item_ref = item.get("Ref", "")
            
            # Check ref matching with various patterns
            if self._is_reference_match(ref, item_ref):
                # Check if this could be a screen with relaxed rules
                if self._could_be_screen_relaxed(item):
                    potential_screens.append(item)
                    logger.info(f"Found potential screen: {ref} -> {item.get('Element_Type', 'Unknown')}")
        
        return potential_screens
    
    def _could_be_screen_relaxed(self, item: Dict) -> bool:
        """Relaxed screen detection"""
        
        ref = item.get("Ref", "")
        
        # Check flags first
        if item.get("Is_GIESTA_Product", False):
            logger.info(f"RELAXED - GIESTA: {ref} - NOT screen")
            return False
        
        if item.get("Is_Screen_Product", None) is True:
            logger.info(f"RELAXED - EXPLICIT SCREEN: {ref}")
            return True
        
        # Continue with relaxed detection...
        element_type = item.get("Element_Type", "")
        description = item.get("Description", "")
        
        # Safe screen indicators
        safe_indicators = ['SCREEN', 'MESH', 'INSECT', 'NET']
        text_to_check = f"{element_type} {description}".upper()
        
        for indicator in safe_indicators:
            if indicator in text_to_check:
                logger.info(f"RELAXED SAFE MATCH '{indicator}': {element_type}")
                return True
        
        # Unknown products with dimensions but no glass
        if element_type == "Unknown" or not element_type.strip():
            has_dimensions = self._has_width_and_height(item)
            has_glass = self._has_glass_data(item)
            
            if has_dimensions and not has_glass:
                logger.info(f"RELAXED UNKNOWN with dimensions, no glass: {element_type}")
                return True
        
        return False
    
    def _is_reference_match(self, ref1: str, ref2: str) -> bool:
        """Strict reference matching - match ได้เฉพาะกรณี exact เท่านั้น"""
        if not ref1 or not ref2:
            return False

        return ref1 == ref2

    
    def _normalize_screen_requirement(self, screen_value: str) -> bool:
        """แปลงค่า screen requirement เป็น boolean"""
        if not screen_value:
            return False
        
        normalized = str(screen_value).lower().strip()
        return normalized in ['yes', 'y', 'true', '1', 'มี', 'ใช่']
    
    def _create_comparison_result(self, ref: str, site_has_screen: bool, 
                                 ele_has_screen: bool, ele_screen_items: List[Dict],
                                 site_item: Dict) -> Dict:
        """สร้างผลการเปรียบเทียบ"""
        
        # กำหนด status
        if site_has_screen and ele_has_screen:
            status = "Match"
            notes = f"Both require screen ({len(ele_screen_items)} items in ELE)"
        elif not site_has_screen and not ele_has_screen:
            status = "Match"
            notes = "Both don't require screen"
        elif site_has_screen and not ele_has_screen:
            status = "Missing Screen"
            notes = "Site requires screen but not found in ELE"
        else:
            status = "Extra Screen"
            notes = f"ELE has screen but Site doesn't require it ({len(ele_screen_items)} items)"
        
        # รายละเอียด ELE
        ele_screen_details = self._format_ele_screen_details(ele_screen_items)
        
        # รวมข้อมูลจาก site survey
        result = {
            "Ref": ref,
            "Site_Screen": "Yes" if site_has_screen else "No",
            "ELE_Screen": "Yes" if ele_has_screen else "No",
            "ELE_Screen_Details": ele_screen_details,
            "Status": status,
            "Notes": notes,
            "Site_Product_Type": site_item.get("Product Type", ""),
            "Site_Opening_Size": f"{site_item.get('Survey_Wo', 0)}x{site_item.get('Survey_Ho', 0)}",
            "Site_Page": site_item.get("Page", "")
        }
        
        return result
    
    def _format_ele_screen_details(self, ele_screen_items: List[Dict]) -> str:
        """จัดรูปแบบรายละเอียด ELE screen items"""
        if not ele_screen_items:
            return "-"
        
        details = []
        for item in ele_screen_items:
            element_type = item.get('Element_Type', 'Screen')
            description = item.get('Description', '')
            page = item.get('Page', '?')
            
            detail = f"{element_type}"
            if description and len(description) < 50:
                detail += f" ({description})"
            detail += f" [Page {page}]"
            
            details.append(detail)
        
        return "; ".join(details)


class SiteDataExtractor:
    """Site Survey Data Extractor"""
    
    @staticmethod
    def extract_site_survey_data(file_path: str) -> List[Dict]:
        """สกัดข้อมูลจาก Site Survey PDF"""
        results = []
        
        try:
            with pdfplumber.open(file_path) as pdf:
                logger.info(f"Processing site survey PDF with {len(pdf.pages)} pages")
                
                for page_num, page in enumerate(pdf.pages, 1):
                    # ลองสกัดจาก text ทั้งหน้าก่อน
                    text_results = SiteDataExtractor._extract_from_text_comprehensive(page, page_num)
                    results.extend(text_results)
                    
                    # ถ้ายังได้น้อย ลองสกัดจาก table
                    if len(text_results) == 0:
                        table_results = SiteDataExtractor._extract_from_tables(page, page_num)
                        results.extend(table_results)
            
            # Remove duplicates based on Ref
            seen_refs = set()
            unique_results = []
            for result in results:
                ref = result.get("Ref", "")
                if ref and ref not in seen_refs:
                    seen_refs.add(ref)
                    unique_results.append(result)
            
            logger.info(f"Extracted {len(unique_results)} unique items from site survey")
            
            # Debug: แสดงข้อมูล insect screen ที่อ่านได้
            logger.info("=== Site Survey Insect Screen Summary ===")
            for item in unique_results:
                ref = item.get("Ref", "")
                screen = item.get("Insect screen", "Unknown")
                logger.info(f"  {ref}: Insect Screen = '{screen}'")
            
            return unique_results
                
        except Exception as e:
            logger.error(f"Error extracting site survey data: {e}")
            return []
    
    @staticmethod
    def _extract_from_text_comprehensive(page, page_num: int) -> List[Dict]:
        """สกัดข้อมูลจาก text ของหน้าแบบครอบคลุม - แก้ไข regex"""
        results = []
        
        try:
            text = page.extract_text()
            if not text:
                return results
            
            lines = text.split('\n')
            logger.info(f"Page {page_num}: Processing {len(lines)} lines")
            
            # ค้นหาข้อมูลแบบทีละบรรทัด
            for i, line in enumerate(lines):
                line = line.strip()
                if not line:
                    continue
                
                # หา reference ในบรรทัด - แก้ไข regex pattern
                ref_matches = re.findall(r'\b(?:[WD]A?\d+(?:\.\d+)?|ADD)\b', line)
                
                # กรองเฉพาะ ref ที่ถูกต้อง
                valid_refs = []
                for ref in ref_matches:
                    if SiteDataExtractor._is_valid_reference(ref):
                        valid_refs.append(ref)
                
                if not valid_refs:
                    continue
                
                for ref in valid_refs:
                    logger.info(f"Found valid reference {ref} in line: '{line}'")
                    
                    # หาตัวเลขสำหรับ opening size
                    numbers = re.findall(r'\b(\d+)\b', line)
                    valid_numbers = [int(n) for n in numbers if 50 <= int(n) <= 8000]
                    
                    # หา insect screen status แบบละเอียด
                    insect_screen = SiteDataExtractor._extract_insect_screen_comprehensive(
                        line, lines, i, ref
                    )
                    
                    # หา product type
                    product_type = SiteDataExtractor._extract_product_type_from_context(line, lines, i)
                    
                    # สร้างข้อมูล
                    if len(valid_numbers) >= 2:
                        result = {
                            "Ref": ref,
                            "Series": "WE70",
                            "Product Type": product_type,
                            "Survey_Wo": valid_numbers[0],
                            "Survey_Ho": valid_numbers[1],
                            "Insect screen": insect_screen,
                            "Page": page_num
                        }
                        results.append(result)
                        logger.info(f"Added {ref}: {valid_numbers[0]}x{valid_numbers[1]}, Screen={insect_screen}")
                    elif insect_screen != "No":
                        result = {
                            "Ref": ref,
                            "Series": "WE70",
                            "Product Type": product_type,
                            "Survey_Wo": 0,
                            "Survey_Ho": 0,
                            "Insect screen": insect_screen,
                            "Page": page_num
                        }
                        results.append(result)
                        logger.info(f"Added {ref} (no dimensions): Screen={insect_screen}")
                
        except Exception as e:
            logger.error(f"Error in comprehensive text extraction: {e}")
        
        return results
    
    @staticmethod
    def _is_valid_reference(ref: str) -> bool:
        """ตรวจสอบว่า reference ถูกต้องหรือไม่"""
        if not ref:
            return False
        
        # รายการ ref ที่ไม่ต้องการ
        invalid_refs = {
            "Attention", "W", "WE70", "Airflow", "Wo", "Ho", "Description",
            "Reference", "Code", "Product", "Type", "Survey", "Page", "Window",
            "Door", "Glass", "Height", "Width"
        }
        
        if ref in invalid_refs:
            return False
        
        # ตรวจสอบ pattern ที่ถูกต้อง
        valid_patterns = [
            r'^[WD]A?\d+$',           # W1, W11, D1, D11, WA1, DA1, WA11, DA11
            r'^[WD]A?\d+\.\d+$',      # W1.1, W1.2, D1.1, WA1.1, DA1.1
            r'^ADD$'                  # Special case
        ]
        
        for pattern in valid_patterns:
            if re.match(pattern, ref):
                return True
        
        return False
    
    @staticmethod
    def _extract_insect_screen_comprehensive(line: str, all_lines: List[str], line_index: int, ref: str) -> str:
        """สกัดข้อมูล insect screen แบบครอบคลุม"""
        
        logger.info(f"Checking insect screen for {ref} in line: '{line}'")
        
        line_lower = line.lower()
        
        # 1. ตรวจสอบในบรรทัดปัจจุบันก่อน
        direct_result = SiteDataExtractor._check_direct_screen_indicators_fixed(line_lower, line)
        if direct_result != "Unknown":
            logger.info(f"Direct match for {ref}: {direct_result}")
            return direct_result
        
        # 2. ตรวจสอบในตารางรูปแบบใหม่
        table_result = SiteDataExtractor._check_table_format_screen_fixed(line, ref)
        if table_result != "Unknown":
            logger.info(f"Table format match for {ref}: {table_result}")
            return table_result
        
        # 3. ตรวจสอบใน context รอบๆ
        context_lines = []
        for offset in range(-3, 4):
            idx = line_index + offset
            if 0 <= idx < len(all_lines):
                context_lines.append(all_lines[idx].lower())
        
        context_text = ' '.join(context_lines)
        
        context_result = SiteDataExtractor._check_context_screen_patterns_fixed(context_text, ref, line)
        if context_result != "Unknown":
            logger.info(f"Context match for {ref}: {context_result}")
            return context_result
        
        logger.info(f"No screen info found for {ref}, defaulting to No")
        return "No"
    
    @staticmethod
    def _check_direct_screen_indicators_fixed(line_lower: str, original_line: str) -> str:
        """ตรวจสอบ indicator ตรงๆ ในบรรทัด"""
        
        # แยกคำด้วย whitespace
        words = original_line.split()
        
        # หาตำแหน่งของ "screen" หรือ "insect"
        screen_indices = []
        for i, word in enumerate(words):
            if 'screen' in word.lower() or 'insect' in word.lower():
                screen_indices.append(i)
        
        # ตรวจสอบคำที่อยู่ใกล้ๆ screen
        for screen_idx in screen_indices:
            for offset in [-2, -1, 1, 2]:
                check_idx = screen_idx + offset
                if 0 <= check_idx < len(words):
                    word = words[check_idx].lower().strip('.,')
                    
                    if word in ['yes', 'y', 'มี', 'ใช่']:
                        logger.info(f"Found YES near screen: {words[check_idx]} at position {check_idx}")
                        return "Yes"
                    elif word in ['no', 'n', 'ไม่มี', 'ไม่ใช่']:
                        logger.info(f"Found NO near screen: {words[check_idx]} at position {check_idx}")
                        return "No"
        
        # Pattern แบบตรงไปตรงมา
        direct_patterns = [
            (r'\binsect\s*screen\s*[:\-]?\s*yes\b', "Yes"),
            (r'\binsect\s*screen\s*[:\-]?\s*no\b', "No"),
            (r'\bscreen\s*[:\-]?\s*yes\b', "Yes"),
            (r'\bscreen\s*[:\-]?\s*no\b', "No"),
            (r'\byes\s*insect\s*screen\b', "Yes"),
            (r'\bno\s*insect\s*screen\b', "No"),
            (r'\bมุ้ง\s*[:\-]?\s*มี\b', "Yes"),
            (r'\bมุ้ง\s*[:\-]?\s*ไม่มี\b', "No"),
            (r'\bมี\s*มุ้ง\b', "Yes"),
            (r'\bไม่มี\s*มุ้ง\b', "No"),
            (r'\binsect.*?[:\-]?\s*y\b', "Yes"),
            (r'\binsect.*?[:\-]?\s*n\b', "No"),
            (r'\bscreen.*?[:\-]?\s*y\b', "Yes"),
            (r'\bscreen.*?[:\-]?\s*n\b', "No"),
        ]
        
        for pattern, result in direct_patterns:
            if re.search(pattern, line_lower):
                logger.info(f"Pattern match: {pattern} -> {result}")
                return result
        
        return "Unknown"
    
    @staticmethod
    def _check_table_format_screen_fixed(line: str, ref: str) -> str:
        """ตรวจสอบรูปแบบตาราง"""
        
        # แยกคอลัมน์ด้วยหลายวิธี
        if '\t' in line:
            columns = line.split('\t')
        else:
            columns = re.split(r'\s{2,}', line.strip())
        
        # ทำความสะอาดคอลัมน์
        columns = [col.strip() for col in columns if col.strip()]
        
        logger.info(f"Table columns for {ref}: {columns}")
        
        # หา reference ในบรรทัด
        ref_found = False
        ref_position = -1
        
        for i, col in enumerate(columns):
            if ref in col:
                ref_found = True
                ref_position = i
                break
        
        if not ref_found:
            return "Unknown"
        
        logger.info(f"Found {ref} at position {ref_position}")
        
        # ตรวจสอบคอลัมน์หลังจาก reference
        for i in range(ref_position + 1, len(columns)):
            col_clean = columns[i].lower().strip()
            
            logger.info(f"Checking column {i}: '{col_clean}'")
            
            if col_clean in ['yes', 'y', 'มี', 'ใช่']:
                if i == len(columns) - 1:  # คอลัมน์สุดท้าย
                    logger.info(f"Last column is YES: {col_clean}")
                    return "Yes"
                elif any(keyword in ' '.join(columns).lower() for keyword in ['insect', 'screen', 'มุ้ง']):
                    logger.info(f"Screen column with YES: {col_clean}")
                    return "Yes"
                    
            elif col_clean in ['no', 'n', 'ไม่มี', 'ไม่ใช่']:
                if i == len(columns) - 1:  # คอลัมน์สุดท้าย
                    logger.info(f"Last column is NO: {col_clean}")
                    return "No"
                elif any(keyword in ' '.join(columns).lower() for keyword in ['insect', 'screen', 'มุ้ง']):
                    logger.info(f"Screen column with NO: {col_clean}")
                    return "No"
        
        return "Unknown"
    
    @staticmethod
    def _check_context_screen_patterns_fixed(context_text: str, ref: str, original_line: str) -> str:
        """ตรวจสอบ pattern ใน context"""
        
        # ตรวจสอบในบรรทัดเดียวกันก่อน
        ref_line_patterns = [
            rf'\b{ref.lower()}\s+.*?\s+(yes|y|มี|ใช่)\b',
            rf'\b{ref.lower()}\s+.*?\s+(no|n|ไม่มี|ไม่ใช่)\b',
            rf'\b(yes|y|มี|ใช่)\s+.*?{ref.lower()}\b',
            rf'\b(no|n|ไม่มี|ไม่ใช่)\s+.*?{ref.lower()}\b',
        ]
        
        original_lower = original_line.lower()
        for pattern in ref_line_patterns:
            match = re.search(pattern, original_lower)
            if match:
                answer = match.group(1).lower()
                if answer in ['yes', 'y', 'มี', 'ใช่']:
                    logger.info(f"Same line pattern: {pattern} -> Yes")
                    return "Yes"
                elif answer in ['no', 'n', 'ไม่มี', 'ไม่ใช่']:
                    logger.info(f"Same line pattern: {pattern} -> No")
                    return "No"
        
        # Context patterns
        context_patterns = [
            (rf'\b{ref.lower()}.*?insect\s*screen.*?yes\b', "Yes"),
            (rf'\b{ref.lower()}.*?insect\s*screen.*?no\b', "No"),
            (rf'\b{ref.lower()}.*?screen.*?yes\b', "Yes"),
            (rf'\b{ref.lower()}.*?screen.*?no\b', "No"),
            (rf'\b{ref.lower()}.*?มุ้ง.*?มี\b', "Yes"),
            (rf'\b{ref.lower()}.*?มุ้ง.*?ไม่มี\b', "No"),
            (rf'\byes.*?insect\s*screen.*?{ref.lower()}\b', "Yes"),
            (rf'\bno.*?insect\s*screen.*?{ref.lower()}\b', "No"),
            (rf'\bมี.*?มุ้ง.*?{ref.lower()}\b', "Yes"),
            (rf'\bไม่มี.*?มุ้ง.*?{ref.lower()}\b', "No"),
        ]
        
        for pattern, result in context_patterns:
            if re.search(pattern, context_text):
                logger.info(f"Context pattern match: {pattern} -> {result}")
                return result
        
        return "Unknown"

    @staticmethod
    def _extract_from_tables(page, page_num: int) -> List[Dict]:
        """สกัดข้อมูลจากตาราง - แก้ไข reference validation"""
        results = []
        
        try:
            tables = page.extract_tables()
            if not tables:
                return results
            
            logger.info(f"Page {page_num}: Found {len(tables)} tables")
            
            for table_idx, table in enumerate(tables):
                if not table:
                    continue
                
                logger.info(f"Processing table {table_idx} with {len(table)} rows")
                
                # หา header row และ screen column
                header_row = None
                screen_col_idx = None
                
                for row_idx, row in enumerate(table[:3]):  # ตรวจ 3 แถวแรก
                    if not row:
                        continue
                    
                    for col_idx, cell in enumerate(row):
                        if cell:
                            cell_text = str(cell).upper().strip()
                            if any(keyword in cell_text for keyword in ['INSECT', 'SCREEN', 'มุ้ง']):
                                header_row = row_idx
                                screen_col_idx = col_idx
                                logger.info(f"Found screen column at table[{row_idx}][{col_idx}]: '{cell_text}'")
                                break
                    
                    if screen_col_idx is not None:
                        break
                
                # ประมวลผล data rows
                start_row = (header_row + 1) if header_row is not None else 0
                
                for row_idx in range(start_row, len(table)):
                    row = table[row_idx]
                    if not row or len(row) < 3:
                        continue
                    
                    # ตรวจสอบ reference ในแถว - ใช้ validation ใหม่
                    ref = None
                    for cell in row[:3]:  # ตรวจ 3 คอลัมน์แรก
                        if cell:
                            cell_str = str(cell).strip()
                            # ใช้ validation function ใหม่
                            if SiteDataExtractor._is_valid_reference(cell_str):
                                ref = cell_str
                                break
                    
                    if not ref:
                        continue
                    
                    logger.info(f"Processing table row for {ref}: {row}")
                    
                    # สกัดข้อมูลจากแถว
                    insect_screen = "No"
                    numbers = []
                    product_type = "Unknown"
                    
                    # ตรวจสอบ insect screen
                    if screen_col_idx is not None and screen_col_idx < len(row):
                        screen_cell = row[screen_col_idx]
                        if screen_cell:
                            screen_value = str(screen_cell).strip().lower()
                            if screen_value in ['yes', 'y', '1', 'มี', 'ใช่']:
                                insect_screen = "Yes"
                            elif screen_value in ['no', 'n', '0', 'ไม่มี', 'ไม่ใช่']:
                                insect_screen = "No"
                            logger.info(f"Screen cell for {ref}: '{screen_cell}' -> {insect_screen}")
                    
                    # หาตัวเลขและ product type
                    for cell in row:
                        if cell:
                            cell_str = str(cell).strip()
                            
                            # หาตัวเลข
                            if cell_str.isdigit():
                                num = int(cell_str)
                                if 50 <= num <= 8000:
                                    numbers.append(num)
                            
                            # หา product type
                            cell_lower = cell_str.lower()
                            if any(pt in cell_lower for pt in ['window', 'door', 'casement', 'sliding', 'awning', 'fixed']):
                                product_type = cell_str.title()
                    
                    # สร้างผลลัพธ์
                    result = {
                        "Ref": ref,
                        "Series": "WE70",
                        "Product Type": product_type,
                        "Survey_Wo": numbers[0] if len(numbers) >= 1 else 0,
                        "Survey_Ho": numbers[1] if len(numbers) >= 2 else 0,
                        "Insect screen": insect_screen,
                        "Page": page_num
                    }
                    results.append(result)
                    logger.info(f"Table extracted {ref}: Screen={insect_screen}")
        
        except Exception as e:
            logger.error(f"Error extracting from tables: {e}")
        
        return results
    
    @staticmethod
    def _extract_product_type_from_context(line: str, all_lines: List[str], line_index: int) -> str:
        """สกัด product type จาก context"""
        
        # ค้นหาใน line ปัจจุบัน
        line_lower = line.lower()
        
        product_types = [
            ('single casement window', 'Single Casement Window'),
            ('casement window', 'Casement Window'),
            ('sliding window', 'Sliding Window'),
            ('sliding door', 'Sliding Door'),
            ('awning window', 'Awning Window'),
            ('fixed window', 'Fixed Window'),
            ('fix window', 'Fixed Window'),
            ('airflow door', 'Airflow Door'),
            ('giesta', 'Giesta Door')
        ]
        
        for pattern, product_type in product_types:
            if pattern in line_lower:
                return product_type
        
        # ค้นหาใน context รอบๆ
        context_lines = []
        for offset in range(-2, 3):
            idx = line_index + offset
            if 0 <= idx < len(all_lines):
                context_lines.append(all_lines[idx].lower())
        
        context_text = ' '.join(context_lines)
        
        for pattern, product_type in product_types:
            if pattern in context_text:
                return product_type
        
        return "Unknown"


class ELEDataExtractor:
    """Enhanced ELE Data Extractor with proper table extraction"""
    
    @staticmethod
    def extract_ele_data(file_path: str) -> List[Dict]:
        """Extract detailed ELE data with proper screen detection"""
        extracted = []
        
        logger.info("Starting Enhanced ELE data extraction with proper screen detection")
        
        try:
            with pdfplumber.open(file_path) as pdf:
                for page_num, page in enumerate(pdf.pages, 1):
                    logger.info(f"Processing ELE page {page_num}")
                    
                    # Extract all text to identify special pages
                    page_text = page.extract_text() or ""
                    
                    # Check if this is a GIESTA page (only for GIESTA detection)
                    is_giesta_page = "GIESTA" in page_text.upper()
                    giesta_description = ""
                    
                    if is_giesta_page:
                        lines = page_text.splitlines()
                        for line in lines:
                            if "GIESTA" in line.upper():
                                giesta_description = line.strip()
                                logger.info(f"Found GIESTA description: {giesta_description}")
                                break
                    
                    # Check if this page has INSECT SCREEN (separate from GIESTA)
                    has_insect_screen = "INSECT SCREEN FOR SASH PRODUCT" in page_text.upper()
                    
                    # Extract tables
                    tables = page.extract_tables()
                    
                    if tables:
                        page_items = ELEDataExtractor._extract_from_tables(tables, page_num)
                        
                        # Apply special detection rules
                        for item in page_items:
                            ref = item.get("Ref", "")
                            if ref and ref != "Wo":  # Skip header rows
                                
                                # GIESTA DETECTION (only for actual GIESTA products)
                                if is_giesta_page and giesta_description and ref.startswith('D'):
                                    item["Element_Type"] = "GIESTA_DOOR"
                                    item["Description"] = giesta_description
                                    item["Is_GIESTA_Product"] = True
                                    item["Is_Screen_Product"] = False
                                    logger.info(f"GIESTA detected for {ref}: {giesta_description}")
                                
                                # SCREEN DETECTION (for INSECT SCREEN pages)
                                elif has_insect_screen:
                                    # This page has insect screen products
                                    element_type = item.get("Element_Type", "")
                                    
                                    # Mixed codes that could be screens on insect screen pages
                                    if any(code in element_type.upper() for code in ['BXC', 'BA7', 'EM2']):
                                        item["Element_Type"] = f"SCREEN_{element_type}"
                                        item["Description"] = "INSECT SCREEN FOR SASH PRODUCT"
                                        item["Is_Screen_Product"] = True
                                        logger.info(f"Screen detected for {ref}: {element_type}")
                        
                        extracted.extend(page_items)
                    else:
                        # Fallback to text extraction
                        text_items = ELEDataExtractor._extract_from_text(page_text, page_num)
                        extracted.extend(text_items)
            
        except Exception as e:
            logger.error(f"Error extracting ELE data: {e}")
        
        logger.info(f"Total ELE items extracted: {len(extracted)}")
        return extracted
    
    @staticmethod
    def _extract_from_tables(tables: List, page_num: int) -> List[Dict]:
        """Extract data from ELE tables - แก้ไข reference validation"""
        results = []
        
        for table_idx, table in enumerate(tables):
            if not table or len(table) < 2:
                continue
            
            logger.info(f"Processing ELE table {table_idx} on page {page_num}")
            
            # Find header row and reference column
            header_row_idx = None
            ref_col_idx = None
            
            for row_idx, row in enumerate(table[:3]):
                if not row:
                    continue
                
                for col_idx, cell in enumerate(row):
                    if cell and str(cell).strip().upper() in ['REFERENCE CODE', 'REF', 'NO']:
                        header_row_idx = row_idx
                        ref_col_idx = col_idx
                        break
                
                if header_row_idx is not None:
                    break
            
            if header_row_idx is None:
                # Try to find references in first few rows anyway
                for row_idx, row in enumerate(table):
                    if not row:
                        continue
                    
                    for col_idx, cell in enumerate(row):
                        if cell:
                            cell_str = str(cell).strip()
                            # ใช้ validation ที่เข้มงวดขึ้น
                            if ELEDataExtractor._is_valid_reference_ele(cell_str):
                                ref_col_idx = col_idx
                                break
                    
                    if ref_col_idx is not None:
                        break
            
            # Extract data rows
            start_row = (header_row_idx + 1) if header_row_idx is not None else 0
            
            for row_idx in range(start_row, len(table)):
                row = table[row_idx]
                if not row or len(row) < 3:
                    continue
                
                # Find reference in this row - ใช้ validation ใหม่
                ref = None
                ref_found_col = None
                
                for col_idx, cell in enumerate(row):
                    if cell:
                        cell_str = str(cell).strip()
                        if ELEDataExtractor._is_valid_reference_ele(cell_str):
                            ref = cell_str
                            ref_found_col = col_idx
                            break
                
                if not ref:
                    continue
                
                # Extract all data from this row
                row_data = ELEDataExtractor._extract_row_data(row, ref, page_num)
                
                if row_data:
                    results.append(row_data)
                    logger.info(f"Extracted ELE data for {ref}: {row_data.get('Element_Type', 'Unknown')}")
        
        return results
    
    @staticmethod
    def _is_valid_reference_ele(ref: str) -> bool:
        """ตรวจสอบว่า reference ใน ELE ถูกต้องหรือไม่"""
        if not ref:
            return False
        
        # รายการ ref ที่ไม่ต้องการใน ELE
        invalid_refs = {
            "Attention", "W", "WE70", "Airflow", "Wo", "Ho", "Description",
            "Reference", "Code", "Product", "Type", "Survey", "Page", "Window",
            "Door", "Glass", "Height", "Width", "Element", "Qty", "Quantity"
        }
        
        if ref in invalid_refs:
            return False
        
        # ตรวจสอบ pattern ที่ถูกต้อง
        valid_patterns = [
            r'^[WD]A?\d+$',           # W1, W11, D1, D11, WA1, DA1
            r'^[WD]A?\d+\.\d+$',      # W1.1, W1.2, D1.1, WA1.1, DA1.1
            r'^ADD$'                  # Special case
        ]
        
        for pattern in valid_patterns:
            if re.match(pattern, ref):
                return True
        
        return False
    
    @staticmethod
    def _extract_row_data(row: List, ref: str, page_num: int) -> Dict:
        """Extract detailed data from a table row - FIXED VERSION"""
        
        # Initialize result
        result = {
            "Ref": ref,
            "Element_Type": "Unknown",
            "Description": "",
            "Page": page_num,
            "Source": "ELE_Table",
            "Is_GIESTA_Product": False,
            "Is_Screen_Product": False,
            "Row_Glass_Data": {}  # เก็บ glass data เฉพาะ row นี้
        }
        
        # First pass: Check for GIESTA products
        is_giesta_product = False
        for cell in row:
            if cell and "GIESTA" in str(cell).upper():
                is_giesta_product = True
                result["Is_GIESTA_Product"] = True
                result["Element_Type"] = "GIESTA_DOOR"
                result["Description"] = f"GIESTA product: {cell}"
                logger.info(f"GIESTA DETECTED in row data: {cell} for {ref}")
                break
        
        # Process all cell data
        row_has_meaningful_glass = False
        element_type_found = ""
        
        for col_idx, cell in enumerate(row):
            if cell and str(cell).strip():
                cell_value = str(cell).strip()
                
                # Skip reference column
                if col_idx == 0 or cell_value == ref:
                    continue
                
                # Check for dimensions (numbers between 200-8000)
                if cell_value.isdigit():
                    num_value = int(cell_value)
                    if 200 <= num_value <= 8000:
                        if f"Width_{col_idx}" not in result:
                            result[f"Width_{col_idx}"] = num_value
                        elif f"Height_{col_idx}" not in result:
                            result[f"Height_{col_idx}"] = num_value
                
                # Skip screen detection if already identified as GIESTA
                if is_giesta_product:
                    continue
                
                cell_upper = cell_value.upper()
                
                # Store element type codes
                if any(code in cell_upper for code in ['MSH', 'MESH', 'SCREEN', 'INSECT', 'BDF', 'BDN', 'BA7', 'BXC', 'ECA', 'EF1', 'EM2', 'EVV', 'FFA', 'WD6']):
                    if result["Element_Type"] == "Unknown":
                        result["Element_Type"] = cell_value
                        element_type_found = cell_value
                        logger.info(f"Element type found: {cell_value} for {ref}")
                
                # Glass data detection - เก็บเฉพาะใน row นี้และตรวจความหมาย
                if any(pattern in cell_upper for pattern in ['GLASS', 'GW', 'GH']):
                    result["Row_Glass_Data"][f"Glass_{col_idx}"] = cell_value
                    # ตรวจว่าเป็น glass data ที่มีความหมายหรือไม่
                    if cell_value and str(cell_value) != '0' and str(cell_value).lower() not in ['none', 'null', '', '-', 'n/a']:
                        row_has_meaningful_glass = True
                        logger.info(f"Meaningful glass data in THIS ROW: {cell_upper} = {cell_value}")
                
                # ตรวจตัวเลขที่อาจเป็น glass thickness (3-50mm)
                elif cell_value.isdigit():
                    num_value = int(cell_value)
                    if 3 <= num_value <= 50:  # อาจเป็น glass thickness
                        result["Row_Glass_Data"][f"Thickness_{col_idx}"] = num_value
                        row_has_meaningful_glass = True
                        logger.info(f"Possible glass thickness in THIS ROW: {num_value}mm")
                
                # Other text data
                elif len(cell_value) > 1 and not cell_value.isdigit():
                    if result["Description"] == "":
                        result["Description"] = cell_value
                    else:
                        result["Description"] += f" | {cell_value}"
        
        # ตัดสินใจว่าเป็น screen หรือไม่ (เฉพาะเมื่อไม่ใช่ GIESTA)
        if not is_giesta_product and element_type_found:
            result["Is_Screen_Product"] = ELEDataExtractor._determine_screen_by_element_and_glass(
                element_type_found, row_has_meaningful_glass, ref
            )
            logger.info(f"Screen decision for {ref} {element_type_found}: {result['Is_Screen_Product']} (glass={row_has_meaningful_glass})")
        
        # Final check: If GIESTA product, ensure it's not marked as screen
        if result["Is_GIESTA_Product"]:
            result["Is_Screen_Product"] = False
        
        return result
    
    @staticmethod 
    def _determine_screen_by_element_and_glass(element_type: str, has_glass: bool, ref: str) -> bool:
        """ตัดสินใจว่าเป็น screen หรือไม่ โดยดูจาก element type และ glass data ใน row เดียวกัน"""
        
        element_upper = element_type.upper()
        
        logger.info(f"=== SCREEN DECISION for {ref} ===")
        logger.info(f"Element: {element_type}, Has Glass: {has_glass}")
        
        # Pure screen codes - เป็น screen แน่นอน
        if any(code in element_upper for code in ['MSH', 'MESH', 'SCREEN', 'INSECT']):
            logger.info(f"PURE SCREEN: {element_type}")
            return True
        
        # Pure window/door codes - ไม่เป็น screen แน่นอน  
        if any(code in element_upper for code in ['ECA', 'EF1', 'EM2', 'EVV', 'FFA', 'WD6']):
            logger.info(f"PURE WINDOW/DOOR: {element_type}")
            return False
        
        # Mixed codes - ตัดสินใจจาก glass data ใน row เดียวกัน
        if any(code in element_upper for code in ['BDF', 'BDN', 'BA7', 'BXC']):
            if has_glass:
                logger.info(f"MIXED CODE + GLASS: {element_type} -> NOT SCREEN")
                return False
            else:
                logger.info(f"MIXED CODE + NO GLASS: {element_type} -> IS SCREEN")  
                return True
        
        logger.info(f"UNKNOWN TYPE: {element_type} -> NOT SCREEN")
        return False
        
    @staticmethod
    def _extract_from_text(text: str, page_num: int) -> List[Dict]:
        """Enhanced text extraction with GIESTA detection"""
        results = []
        
        # Check for GIESTA in text
        if "GIESTA" in text.upper():
            lines = text.splitlines()
            giesta_description = ""
            
            # Find GIESTA description
            for line in lines:
                if "GIESTA" in line.upper():
                    giesta_description = line.strip()
                    break
            
            # Find references in text - ใช้ validation ใหม่
            refs = re.findall(r'\b[WD]A?\d+(?:\.\d+)?\b', text)
            unique_refs = []
            for ref in refs:
                if ELEDataExtractor._is_valid_reference_ele(ref):
                    unique_refs.append(ref)
            
            # Create GIESTA entries
            for ref in unique_refs:
                results.append({
                    "Ref": ref,
                    "Element_Type": "GIESTA_DOOR",
                    "Description": giesta_description,
                    "Page": page_num,
                    "Source": "ELE_Text",
                    "Is_GIESTA_Product": True,
                    "Is_Screen_Product": False
                })
                logger.info(f"Created GIESTA entry from text: {ref}")
            
            return results
        
        # Original fallback logic for non-GIESTA pages
        refs = re.findall(r'\b[WDA]A?\d+(?:\.\d+)?\b', text)
        unique_refs = []
        for ref in refs:
            if ELEDataExtractor._is_valid_reference_ele(ref):
                unique_refs.append(ref)
        
        if not unique_refs:
            return results
        
        logger.info(f"Page {page_num} - Found valid references in text: {unique_refs}")
        
        element_type = ELEDataExtractor._extract_element_type(text)
        description = ELEDataExtractor._extract_description(text)
        
        for ref in unique_refs:
            results.append({
                "Ref": ref,
                "Element_Type": element_type,
                "Description": description,
                "Page": page_num,
                "Source": "ELE_Text"
            })
        
        return results
    
    @staticmethod
    def _extract_element_type(text: str) -> str:
        """Extract element type from text"""
        lines = text.splitlines()

        text_upper = text.upper()
        if "GIESTA" in text_upper:
            # หา line ที่มี GIESTA
            for line in lines:
                if "GIESTA" in line.upper():
                    return line.strip()
        
        # Look for Description line
        for line in lines:
            line_clean = line.strip()
            if "Description" in line_clean:
                description = line_clean.replace("Description", "").strip()
                description = ELEDataExtractor._clean_description(description)
                
                if description and len(description) > 3:
                    return description
        
        # Look for product patterns in text
        text_upper = text.upper()
        
        product_patterns = [
            (r'INSECT SCREEN FOR SASH PRODUCT', 'INSECT SCREEN FOR SASH PRODUCT'),
            (r'SCREEN AWNING WINDOW', 'Screen Awning Window'),
            (r'SG CASEMENT WINDOW', 'SG Casement Window'),
            (r'SINGLE CASEMENT WINDOW', 'Single Casement Window'),
            (r'AWNING WINDOW', 'Awning Window'),
            (r'SLIDING WINDOW', 'Sliding Window'),
            (r'SLIDING DOOR', 'Sliding Door'),
            (r'FIXED WINDOW', 'Fixed Window'),
            (r'AIRFLOW DOOR', 'Airflow Door'),
            (r'GIESTA.*?OUT-SWING', 'GIESTA Out-swing')
        ]
        
        for pattern, element_type in product_patterns:
            if re.search(pattern, text_upper):
                return element_type
        
        return "Unknown Element"
    
    @staticmethod
    def _extract_description(text: str) -> str:
        """Extract description from text"""
        lines = text.splitlines()
        
        for line in lines:
            line_clean = line.strip()
            if "Description" in line_clean:
                description = line_clean.replace("Description", "").strip()
                return ELEDataExtractor._clean_description(description)
        
        # If no Description line found, use text from header
        text_lines = [line.strip() for line in lines if line.strip()]
        
        # Look for product name or title
        for line in text_lines[:10]:  # Check first 10 lines
            if any(keyword in line.upper() for keyword in [
                'WINDOW', 'DOOR', 'SCREEN', 'AWNING', 'SLIDING', 'CASEMENT', 'FIXED'
            ]):
                return line
        
        return ""
    
    @staticmethod
    def _clean_description(description: str) -> str:
        """Clean description text"""
        if not description:
            return description
        
        # Remove unwanted phrases
        phrases_to_remove = [
            "Customer approve", "customer approve",
            "Customer Approve", "CUSTOMER APPROVE"
        ]
        
        cleaned = description
        for phrase in phrases_to_remove:
            cleaned = cleaned.replace(phrase, "").strip()
        
        return " ".join(cleaned.split())


# Flask Routes
@app.route('/')
def index():
    """หน้าแรกของแอปพลิเคชั่น"""
    return render_template('insect_screen_index.html')

@app.route('/upload', methods=['POST'])
def upload_files():
    """อัปโหลดและประมวลผลไฟล์"""
    try:
        # ตรวจสอบการอัปโหลดไฟล์
        if 'site_survey' not in request.files or 'ele_file' not in request.files:
            return jsonify({'error': 'กรุณาเลือกไฟล์ทั้งสองไฟล์'}), 400
        
        site_file = request.files['site_survey']
        ele_file = request.files['ele_file']
        
        if site_file.filename == '' or ele_file.filename == '':
            return jsonify({'error': 'กรุณาเลือกไฟล์ทั้งสองไฟล์'}), 400
        
        # บันทึกไฟล์อย่างปลอดภัย
        site_filename = secure_filename(site_file.filename)
        ele_filename = secure_filename(ele_file.filename)
        
        site_path = os.path.join(app.config['UPLOAD_FOLDER'], site_filename)
        ele_path = os.path.join(app.config['UPLOAD_FOLDER'], ele_filename)
        
        site_file.save(site_path)
        ele_file.save(ele_path)
        
        logger.info(f"Files saved: {site_filename}, {ele_filename}")
        
        # ประมวลผลไฟล์
        site_data = SiteDataExtractor.extract_site_survey_data(site_path)
        ele_data = ELEDataExtractor.extract_ele_data(ele_path)
        
        if not site_data and not ele_data:
            return jsonify({'error': 'ไม่สามารถดึงข้อมูลจากไฟล์ PDF ได้'}), 400
        
        # เปรียบเทียบข้อมูลมุ้ง
        comparator = InsectScreenComparator()
        insect_screen_results = comparator.compare_insect_screens(site_data, ele_data)
        
        # สร้างสถิติสรุป
        total_items = len(insect_screen_results)
        matches = len([r for r in insect_screen_results if 'Match' in r['Status']])
        mismatches = total_items - matches
        
        summary = {
            'total_items': total_items,
            'matches': matches,
            'mismatches': mismatches,
            'match_rate': round((matches / total_items * 100), 1) if total_items > 0 else 0
        }
        
        # ลบไฟล์ชั่วคราว
        try:
            os.remove(site_path)
            os.remove(ele_path)
        except Exception as e:
            logger.warning(f"Could not remove temporary files: {e}")
        
        logger.info(f"Processing completed. Total items: {total_items}")
        
        return jsonify({
            'success': True,
            'summary': summary,
            'insect_screen_results': insect_screen_results,
            'site_data': site_data,
            'ele_data': ele_data
        })
        
    except Exception as e:
        logger.error(f"Error in upload_files: {e}")
        return jsonify({'error': f'เกิดข้อผิดพลาด: {str(e)}'}), 500

@app.route('/download_excel', methods=['POST'])
def download_excel():
    """ดาวน์โหลดผลลัพธ์เป็น Excel"""
    try:
        data = request.json
        insect_screen_results = data.get('insect_screen_results', [])
        site_data = data.get('site_data', [])
        ele_data = data.get('ele_data', [])
        
        # สร้างไฟล์ Excel ในหน่วยความจำ
        output = io.BytesIO()
        
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            # Insect screen comparison sheet
            if insect_screen_results:
                df_insect_screen = pd.DataFrame(insect_screen_results)
                df_insect_screen.to_excel(writer, sheet_name='Insect_Screen_Comparison', index=False)
            
            # Site data sheet
            if site_data:
                df_site = pd.DataFrame(site_data)
                df_site.to_excel(writer, sheet_name='Site_Survey', index=False)
            
            # ELE data sheet
            if ele_data:
                df_ele = pd.DataFrame(ele_data)
                df_ele.to_excel(writer, sheet_name='ELE_Data', index=False)
        
        output.seek(0)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"Insect_Screen_Comparison_{timestamp}.xlsx"
        
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename
        )
        
    except Exception as e:
        logger.error(f"Error creating Excel file: {e}")
        return jsonify({'error': f'เกิดข้อผิดพลาดในการสร้างไฟล์ Excel: {str(e)}'}), 500

def process_insect_screens(site_file_path, ele_file_path):
    """
    ฟังก์ชันสำหรับประมวลผล insect screens จากไฟล์ site survey และ ELE
    ใช้สำหรับเรียกจาก main5.py
    """
    try:
        logger.info("Starting insect screen processing...")
        
        # Extract data from both files
        site_data = SiteDataExtractor.extract_site_survey_data(site_file_path)
        ele_data = ELEDataExtractor.extract_ele_data(ele_file_path)
        
        if not site_data:
            logger.warning("No site data extracted for insect screen analysis")
            return {
                'success': False, 
                'error': 'No data extracted from site survey file',
                'results': []
            }
        
        # Create comparator and process
        comparator = InsectScreenComparator()
        insect_screen_results = comparator.compare_insect_screens(site_data, ele_data)
        
        logger.info(f"Insect screen processing completed. Found {len(insect_screen_results)} results")
        
        return {
            'success': True,
            'results': insect_screen_results,
            'site_data': site_data,
            'ele_data': ele_data
        }
        
    except Exception as e:
        logger.error(f"Error in process_insect_screens: {e}")
        return {
            'success': False,
            'error': str(e),
            'results': []
        }
