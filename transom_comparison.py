# use of main5.py

# ele_only_transom_extractor.py - Extract Transom data from ELE only
import pdfplumber
import pandas as pd
import re
import logging
from collections import Counter

logger = logging.getLogger(__name__)

class ELETransomExtractor:
    """Extract Transom series information from ELE files only"""

    @staticmethod
    def extract_ele_transoms(file_path, use_context_detection=False):
        """Extract all transom data from ELE file"""
        results = []
        ref_data_lookup = {}
        all_refs_found = set()  # เก็บ refs ทั้งหมดที่พบ
        
        try:
            with pdfplumber.open(file_path) as pdf:
                logger.info(f"Processing ELE for transoms: {len(pdf.pages)} pages")
                
                for page_num, page in enumerate(pdf.pages, 1):
                    text = page.extract_text() or ""
                    
                    # Skip INSECT SCREEN pages
                    if ELETransomExtractor._is_insect_screen_page(text):
                        logger.info(f"Skipping INSECT SCREEN page {page_num}")
                        continue
                    
                    # Find reference codes in text first
                    refs_in_text = re.findall(r'\b(?:[WD]A?\d+(?:\.\d+)?|ADD)\b', text)
                    if not refs_in_text:
                        continue
                    
                    # เก็บ refs ทั้งหมดที่พบ
                    all_refs_found.update(refs_in_text)
                    
                    logger.info(f"Page {page_num}: Found refs in text: {refs_in_text}")
                    
                    # ... (ส่วนการตรวจจับ transom เหมือนเดิม) ...
                    
                    # Extract tables
                    tables = page.extract_tables()
                    
                    # Strategy 1: Extract from tables with enhanced detection
                    transom_refs_from_table = ELETransomExtractor._extract_transoms_from_tables(tables, refs_in_text)
                    
                    # Strategy 2: Extract from text patterns
                    transom_refs_from_text = ELETransomExtractor._extract_transoms_from_text_patterns(text, refs_in_text)
                    
                    # Strategy 3: Check for any reference that might be transom based on context (optional)
                    transom_refs_context = {}
                    if use_context_detection:
                        transom_refs_context = ELETransomExtractor._check_refs_by_context(text, refs_in_text)
                    
                    # Combine results from all strategies
                    all_transom_refs = {}
                    all_transom_refs.update(transom_refs_from_table)
                    all_transom_refs.update(transom_refs_from_text)
                    all_transom_refs.update(transom_refs_context)
                    
                    if all_transom_refs:
                        logger.info(f"Page {page_num}: Found {len(all_transom_refs)} transoms: {list(all_transom_refs.keys())}")
                    
                    # For each confirmed transom reference, extract series information
                    for ref, transom_info in all_transom_refs.items():
                        # Extract series information
                        transom_series = ELETransomExtractor._extract_explicit_transom_from_text(text)
                        main_series = ELETransomExtractor._extract_main_series_from_page(text)
                        
                        # Initialize ref data if not exists
                        if ref not in ref_data_lookup:
                            ref_data_lookup[ref] = {
                                'explicit_transom': [],
                                'main_series': [],
                                'pages': [],
                                'descriptions': [],
                                'detection_methods': [],
                                'is_transom': True  # เพิ่มฟิลด์นี้
                            }
                        
                        # Add series data
                        if transom_series:
                            ref_data_lookup[ref]['explicit_transom'].append(transom_series)
                        if main_series:
                            ref_data_lookup[ref]['main_series'].append(main_series)
                        
                        ref_data_lookup[ref]['pages'].append(page_num)
                        ref_data_lookup[ref]['descriptions'].append(transom_info.get('description', ''))
                        
                        # Track detection method
                        if ref in transom_refs_from_table:
                            ref_data_lookup[ref]['detection_methods'].append('Table')
                        if ref in transom_refs_from_text:
                            ref_data_lookup[ref]['detection_methods'].append('Text Pattern')
                        if ref in transom_refs_context:
                            ref_data_lookup[ref]['detection_methods'].append('Context')
            
            # เพิ่ม refs ที่ไม่ใช่ transom
            for ref in all_refs_found:
                if ref not in ref_data_lookup:
                    ref_data_lookup[ref] = {
                        'explicit_transom': [],
                        'main_series': [],
                        'pages': [],
                        'descriptions': [],
                        'detection_methods': [],
                        'is_transom': False
                    }
            
            # Process collected data
            for ref, data in ref_data_lookup.items():
                # กำหนด Status
                status = "Yes" if data['is_transom'] else "No"
                
                if data['is_transom']:
                    # Choose best transom series
                    final_transom_series = ""
                    has_explicit_transom = False
                    
                    # Priority: explicit transom > main series
                    if data['explicit_transom']:
                        counter = Counter(data['explicit_transom'])
                        final_transom_series = counter.most_common(1)[0][0]
                        has_explicit_transom = True
                        logger.info(f"Using explicit transom for {ref}: {final_transom_series}")
                    elif data['main_series']:
                        counter = Counter(data['main_series'])
                        final_transom_series = counter.most_common(1)[0][0]
                        logger.info(f"Using main series for {ref}: {final_transom_series}")
                    
                    # Get best description and detection methods
                    descriptions = [desc for desc in data['descriptions'] if desc.strip()]
                    best_description = descriptions[0] if descriptions else ''
                    detection_methods = list(set(data['detection_methods']))  # Remove duplicates
                    
                    # Determine transom type
                    transom_type = ELETransomExtractor._determine_transom_type(best_description)
                    
                    # Add to results
                    results.append({
                        'Ref': ref,
                        'Status': status,  # เพิ่มฟิลด์ Status
                        'Transom_Series': final_transom_series if final_transom_series else '-',
                        'Main_Series': data['main_series'][0] if data['main_series'] else '',
                        'Has_Explicit_Transom': has_explicit_transom,
                        'Description': best_description,
                        'Transom_Type': transom_type,
                        'Detection_Methods': ', '.join(detection_methods),
                        'Pages': ', '.join(map(str, data['pages']))
                    })
                else:
                    # Non-transom reference
                    results.append({
                        'Ref': ref,
                        'Status': status,  # No
                        'Transom_Series': '-',
                        'Main_Series': '',
                        'Has_Explicit_Transom': False,
                        'Description': 'Not a transom',
                        'Transom_Type': 'Not Transom',
                        'Detection_Methods': 'N/A',
                        'Pages': ''
                    })
                
                logger.info(f"Final result: {ref} = Status: {status}")
                    
        except Exception as e:
            logger.error(f"Error extracting ELE transom data: {e}")
        
        return results
    
    @staticmethod
    def _extract_transoms_from_tables(tables, refs_in_text):
        """Extract transoms from tables with multiple strategies"""
        transom_refs = {}
        
        if not tables:
            return transom_refs
            
        for table_idx, table in enumerate(tables):
            if not table:
                continue
                
            # Strategy 1: Structured table extraction
            structured_results = ELETransomExtractor._extract_from_structured_table(table, refs_in_text)
            transom_refs.update(structured_results)
            
            # Strategy 2: Scan all cells
            scan_results = ELETransomExtractor._scan_table_for_transoms(table, refs_in_text)
            transom_refs.update(scan_results)
            
            # Strategy 3: Row-by-row analysis
            row_results = ELETransomExtractor._extract_row_by_row(table, refs_in_text)
            transom_refs.update(row_results)
        
        return transom_refs
    
    @staticmethod
    def _extract_from_structured_table(table, refs_in_text):
        """Extract from tables with clear structure"""
        transom_refs = {}
        
        # Find header positions
        header_row = None
        ref_col = None
        desc_cols = []
        
        for i, row in enumerate(table):
            if not row:
                continue
                
            row_str = [str(cell).strip().upper() if cell else "" for cell in row]
            
            # Look for headers
            if any("DESCRIPTION" in cell or "DESC" in cell or "PRODUCT" in cell for cell in row_str):
                header_row = i
                for j, cell in enumerate(row_str):
                    if "DESCRIPTION" in cell or "DESC" in cell or "PRODUCT" in cell:
                        desc_cols.append(j)
                    elif "REFERENCE" in cell or "REF" in cell:
                        ref_col = j
                break
        
        # Default positions if no headers
        if header_row is None:
            ref_col = 0
            desc_cols = list(range(1, len(table[0]) if table and table[0] else 0))
        
        # Process data rows
        for i, row in enumerate(table):
            if header_row is not None and i <= header_row:
                continue
            if not row:
                continue
                
            ref = str(row[ref_col]).strip() if ref_col is not None and len(row) > ref_col else ""
            
            if not ref or ref not in refs_in_text:
                continue
            
            # Check descriptions
            for desc_col in desc_cols:
                if len(row) > desc_col and row[desc_col]:
                    description = str(row[desc_col]).strip()
                    if ELETransomExtractor._is_transom_entry(description):
                        transom_refs[ref] = {'description': description}
                        logger.info(f"Table transom: {ref} (Desc: {description})")
                        break
        
        return transom_refs
    
    @staticmethod
    def _scan_table_for_transoms(table, refs_in_text):
        """Scan all table cells for transom patterns"""
        transom_refs = {}
        
        for i, row in enumerate(table):
            if not row:
                continue
                
            for j, cell in enumerate(row):
                if not cell:
                    continue
                    
                cell_str = str(cell).strip()
                
                if ELETransomExtractor._is_transom_entry(cell_str):
                    # Look for reference in same row
                    ref_found = None
                    
                    for k, other_cell in enumerate(row):
                        if other_cell:
                            other_str = str(other_cell).strip()
                            if other_str in refs_in_text:
                                ref_found = other_str
                                break
                    
                    # Check nearby rows if not found
                    if not ref_found:
                        for nearby_i in range(max(0, i-2), min(len(table), i+3)):
                            if nearby_i == i:
                                continue
                            nearby_row = table[nearby_i]
                            if not nearby_row:
                                continue
                            for cell_val in nearby_row:
                                if cell_val:
                                    cell_val_str = str(cell_val).strip()
                                    if cell_val_str in refs_in_text:
                                        ref_found = cell_val_str
                                        break
                            if ref_found:
                                break
                    
                    if ref_found:
                        transom_refs[ref_found] = {'description': cell_str}
                        logger.info(f"Scanned transom: {ref_found} (Desc: {cell_str})")
        
        return transom_refs
    
    @staticmethod
    def _extract_row_by_row(table, refs_in_text):
        """Check each row for ref + description combination"""
        transom_refs = {}
        
        for row in table:
            if not row:
                continue
            
            ref_found = None
            descriptions = []
            
            for cell in row:
                if not cell:
                    continue
                    
                cell_str = str(cell).strip()
                
                if cell_str in refs_in_text:
                    ref_found = cell_str
                elif ELETransomExtractor._is_transom_entry(cell_str):
                    descriptions.append(cell_str)
            
            if ref_found and descriptions:
                transom_refs[ref_found] = {'description': descriptions[0]}
                logger.info(f"Row transom: {ref_found} (Desc: {descriptions[0]})")
        
        return transom_refs
    
    @staticmethod
    def _extract_transoms_from_text_patterns(text, refs_in_text):
        """Extract transoms from text patterns - FIXED VERSION"""
        transom_refs = {}
        
        if not text or not refs_in_text:
            return transom_refs
        
        # Strategy 1: ค้นหาใน Description field (วิธีที่น่าเชื่อถือที่สุด)
        description_patterns = [
            r'Description\s+([^\n\r]+)',
            r'Customer approve\s*\n\s*([^\n\r]+)',
            r'Customer approve\s+([^\n\r]+)',
            r'Description\s*\n\s*([^\n\r]+)',  # เพิ่มกรณีที่มี newline
        ]
        
        for pattern in description_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE | re.MULTILINE)
            for match in matches:
                match = match.strip()
                if match and ELETransomExtractor._is_transom_entry(match):
                    # เชื่อมโยงกับ refs ทั้งหมดในหน้านี้
                    for ref in refs_in_text:
                        if ref not in transom_refs:  # ไม่เขียนทับที่มีอยู่แล้ว
                            transom_refs[ref] = {'description': match}
                            logger.info(f"Text pattern transom from Description: {ref} (Desc: {match})")
        
        # Strategy 2: ค้นหาในบรรทัดที่มี transom indicators
        lines = text.splitlines()
        
        for i, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue
                
            # ตรวจสอบว่าบรรทัดมี transom pattern หรือไม่
            if ELETransomExtractor._is_transom_entry(line):
                # มองหา references ในบรรทัดใกล้เคียง (±3 บรรทัด)
                search_lines = []
                for j in range(max(0, i-3), min(len(lines), i+4)):
                    search_lines.append(lines[j])
                
                search_text = " ".join(search_lines)
                found_refs = re.findall(r'\b(?:[WD]A?\d+(?:\.\d+)?|ADD)\b', search_text)
                
                for ref in found_refs:
                    if ref in refs_in_text and ref not in transom_refs:
                        transom_refs[ref] = {'description': line}
                        logger.info(f"Text pattern transom from line: {ref} (Desc: {line})")
        
        # Strategy 3: Page-level strong indicators
        strong_indicators = [
            r'\d+\s*Sliding\s+[A-Z]\+Fixed',  # "2Sliding B+Fixed"
            r'[A-Z]+\s+[A-Z]+.*L\+Fixed',     # "MS P01 L+Fixed"
            r'\d+\s*Panel.*\+',               # "2Panel...+"
            r'Combination.*\+',               # "Combination...+"
        ]
        
        for indicator_pattern in strong_indicators:
            if re.search(indicator_pattern, text, re.IGNORECASE):
                matches = re.finditer(indicator_pattern, text, re.IGNORECASE)
                for match in matches:
                    # หา context รอบๆ match
                    start = max(0, match.start() - 50)
                    end = min(len(text), match.end() + 50)
                    context = text[start:end].strip()
                    
                    # เชื่อมโยงกับ refs ที่ยังไม่ได้ assign
                    unassigned_refs = [ref for ref in refs_in_text if ref not in transom_refs]
                    for ref in unassigned_refs:
                        transom_refs[ref] = {'description': f"Strong indicator: {match.group()}"}
                        logger.info(f"Text pattern transom from strong indicator: {ref} (Pattern: {match.group()})")
                    
                    if unassigned_refs:
                        break  # assign แค่ครั้งเดียวต่อ pattern
        
        return transom_refs
    
    @staticmethod
    def _check_refs_by_context(text, refs_in_text):
        """Check references by page context (optional)"""
        transom_refs = {}
        
        text_lower = text.lower()
        page_has_transom_context = any(indicator in text_lower for indicator in [
            'transom', 'combination', 'multi panel', 'two panel', 'double panel',
            '(2)', '+', 'plus', 'fixed+', '+fixed'
        ])
        
        if page_has_transom_context:
            logger.info(f"Page has transom context indicators")
            for ref in refs_in_text:
                ref_pattern = r'\b' + re.escape(ref) + r'\b'
                matches = list(re.finditer(ref_pattern, text))
                
                for match in matches:
                    start = max(0, match.start() - 200)
                    end = min(len(text), match.end() + 200)
                    context = text[start:end]
                    
                    if ELETransomExtractor._is_transom_entry(context):
                        context_description = ELETransomExtractor._extract_context_description(context, ref)
                        transom_refs[ref] = {'description': context_description}
                        logger.info(f"Context transom: {ref} (Pattern: {context_description})")
                        break
        
        return transom_refs
    
    @staticmethod
    def _extract_context_description(context, ref):
        """Extract actual transom description from context"""
        lines = context.splitlines()
        
        for line in lines:
            line = line.strip()
            if ELETransomExtractor._is_transom_entry(line) and len(line) < 100:
                return f"Context: {line}"
        
        # Look for patterns
        patterns_found = []
        if '(2)' in context:
            patterns_found.append('(2) pattern')
        if '+' in context:
            patterns_found.append('+ pattern')
        if 'transom' in context.lower():
            patterns_found.append('transom keyword')
        if 'combination' in context.lower():
            patterns_found.append('combination')
        
        if patterns_found:
            return f"Context patterns: {', '.join(patterns_found)}"
        
        return "Context-based detection"
    
    @staticmethod
    def _is_transom_entry(description):
        """Check if entry is a transom - ENHANCED VERSION"""
        if not description:
            return False
            
        description_str = str(description).strip()
        
        # ข้าม insect screen entries
        if 'insect screen' in description_str.lower():
            return False
        
        transom_patterns = [
            # Number patterns
            r'\(2\)', r'\(\s*2\s*\)', r'\(two\)', r'\(TWO\)',
            
            # Plus patterns  
            r'\+', r'plus', r'PLUS',
            
            # Combination patterns
            r'\w+\+\w+', r'fixed\s*\+', r'\+\s*fixed', r'sliding\s*\+', r'\+\s*sliding',
            r'B\+', r'L\+', r'R\+', r'\+Fixed', r'\+Transom', r'Fixed\+', r'Sliding\+',
            
            # Window/Door with numbers
            r'window\s*\(\s*2\s*\)', r'awning\s*\(\s*2\s*\)', r'door\s*\(\s*2\s*\)',
            r'panel\s*\(\s*2\s*\)', r'sliding\s*\(\s*2\s*\)', r'casement\s*\(\s*2\s*\)',
            
            # Explicit keywords
            r'transom', r'TRANSOM', r'combination', r'COMBINATION',
            r'multi\s*panel', r'MULTI\s*PANEL', r'two\s*panel', r'TWO\s*PANEL',
            r'double\s*panel', r'DOUBLE\s*PANEL', r'dual\s*panel', r'DUAL\s*PANEL',
            
            # Complex patterns
            r'Awning\s*window\s*\(2\)', r'Screen\s*Awning\s*window\s*\(2\)',
            r'2\s*Sliding\s*B\+Fixed', r'2\s*panels?\s*sliding', r'2\s*panels?\s*fixed',
            
            # Numbers at start - เพิ่ม patterns สำหรับตัวเลขที่ขึ้นต้น
            r'^\d+\s*Sliding', r'^\d+\s*Panel', r'^\d+\s*Door', r'^\d+\s*Window'
        ]
        
        for pattern in transom_patterns:
            if re.search(pattern, description_str, re.IGNORECASE):
                return True
        
        return False
    
    @staticmethod
    def _determine_transom_type(description):
        """Determine transom type from description"""
        if not description:
            return "Transom (No Description)"
            
        desc_lower = str(description).lower()
        
        if '(2)' in description or '( 2 )' in description:
            if 'window' in desc_lower or 'awning' in desc_lower:
                return "Multiple Window Transom (2)"
            elif 'door' in desc_lower or 'sliding' in desc_lower:
                return "Multiple Door Transom (2)"
            else:
                return "Multiple Panel Transom (2)"
        elif '+' in description or 'plus' in desc_lower:
            return "Plus/Addition Transom (+)"
        elif 'combination' in desc_lower:
            return "Combination Transom"
        elif 'multi panel' in desc_lower or 'two panel' in desc_lower:
            return "Multi Panel Transom"
        elif 'context' in desc_lower:
            return "Context-Detected Transom"
        
        return "Transom (Pattern Detected)"
    
    @staticmethod
    def _is_insect_screen_page(text):
        """Check if page is insect screen page - ENHANCED VERSION"""
        if not text:
            return False
        
        text_lower = text.lower()
        
        # เพิ่ม patterns สำหรับตรวจจับ insect screen pages
        insect_screen_patterns = [
            'insect screen for sash product',
            'description insect screen for sash product',
            'customer approve\ninsect screen for sash product',
            'insect screen for sash',
            r'description\s*\n\s*insect screen for sash product'
        ]
        
        for pattern in insect_screen_patterns:
            if re.search(pattern, text_lower, re.MULTILINE):
                return True
        
        # ตรวจสอบเพิ่มเติมสำหรับ screen awning
        if 'screen awning' in text_lower and 'for sash' in text_lower:
            return True
        
        return False
    
    @staticmethod
    def _debug_page_content(text, page_num, refs_in_text):
        """Debug function to analyze page content"""
        logger.info(f"=== DEBUG PAGE {page_num} ===")
        logger.info(f"Refs found: {refs_in_text}")
        
        # ตรวจสอบ description patterns
        description_patterns = [
            r'Description\s+([^\n\r]+)',
            r'Customer approve\s+([^\n\r]+)',
        ]
        
        for pattern_name, pattern in [("Description field", r'Description\s+([^\n\r]+)'), 
                                    ("Customer approve", r'Customer approve\s+([^\n\r]+)')]:
            matches = re.findall(pattern, text, re.IGNORECASE | re.MULTILINE)
            if matches:
                logger.info(f"Found {pattern_name}: {matches}")
                for match in matches:
                    is_transom = ELETransomExtractor._is_transom_entry(match)
                    logger.info(f"  '{match}' -> Is transom: {is_transom}")
        
        # ตรวจสอบ lines ที่มี transom patterns
        lines = text.splitlines()
        transom_lines = []
        for i, line in enumerate(lines):
            line = line.strip()
            if line and ELETransomExtractor._is_transom_entry(line):
                transom_lines.append((i+1, line))
        
        if transom_lines:
            logger.info(f"Lines with transom patterns: {transom_lines}")
        
    @staticmethod 
    def _extract_explicit_transom_from_text(text):
        """Extract explicit transom series from text"""
        if not text:
            return None
            
        lines = text.splitlines()
        
        transom_patterns = [
            r'Transom\s*[=:]\s*([A-Z0-9\-]+)',
            r'transom\s*[=:]\s*([A-Z0-9\-]+)',
            r'TRANSOM\s*[=:]\s*([A-Z0-9\-]+)'
        ]
        
        for line in lines:
            for pattern in transom_patterns:
                match = re.search(pattern, line, re.IGNORECASE)
                if match:
                    return match.group(1).strip().upper()
        
        return None
    
    @staticmethod
    def _extract_main_series_from_page(text):
        """Extract main series from page text"""
        if not text:
            return ""
        
        text_upper = text.upper()
        
        # Priority series
        primary_series = ['GIESTA', 'ATIS', 'TOSTEM', 'YKK', 'LIXIL']
        for series in primary_series:
            if series in text_upper:
                return series
        
        # WE series
        we_match = re.search(r'WE\s*(\d+)', text_upper)
        if we_match:
            return f"WE{we_match.group(1)}"
        
        # Other patterns
        excluded_codes = ['EP3', 'ET2', 'EM2', 'EVV', 'ECL', 'EF1']
        series_patterns = [r'\b(MS\d*)\b', r'\b(P\d+)\b', r'\b([A-Z]{2,3}\d+)\b']
        
        for pattern in series_patterns:
            matches = re.findall(pattern, text_upper)
            if matches:
                exclude_words = ['THE', 'AND', 'FOR', 'WITH', 'DOOR', 'WINDOW', 'FIXED', 'SLIDING'] + excluded_codes
                valid_matches = [m for m in matches if m not in exclude_words]
                if valid_matches:
                    return valid_matches[0]
        
        return ""

def process_transoms(ele_path, use_context_detection=False):
    """Process ELE transoms only - no comparison with site"""
    try:
        logger.info("=== EXTRACTING TRANSOMS FROM ELE ONLY ===")
        
        # Extract transom data from ELE
        transom_results = ELETransomExtractor.extract_ele_transoms(ele_path, use_context_detection)
        logger.info(f"ELE transom extraction completed: {len(transom_results)} transoms found")
        
        # Calculate summary statistics
        total_transoms = len(transom_results)
        
        # Count by transom type
        type_counts = {}
        for result in transom_results:
            transom_type = result['Transom_Type']
            type_counts[transom_type] = type_counts.get(transom_type, 0) + 1
        
        # Count by detection method
        detection_counts = {}
        for result in transom_results:
            methods = result['Detection_Methods'].split(', ') if result['Detection_Methods'] else ['Unknown']
            for method in methods:
                detection_counts[method] = detection_counts.get(method, 0) + 1
        
        # Count explicit transoms
        explicit_count = len([r for r in transom_results if r['Has_Explicit_Transom']])
        
        # Count by series
        series_counts = {}
        for result in transom_results:
            series = result['Transom_Series']
            if series and series != '-':
                series_counts[series] = series_counts.get(series, 0) + 1
        
        summary = {
            'total_transoms': total_transoms,
            'type_counts': type_counts,
            'detection_counts': detection_counts,
            'series_counts': series_counts,
            'explicit_count': explicit_count,
            'context_detection_used': use_context_detection
        }
        
        # Log summary
        logger.info(f"=== ELE TRANSOM SUMMARY ===")
        logger.info(f"Total transoms: {total_transoms}")
        logger.info(f"Transom types: {type_counts}")
        logger.info(f"Detection methods: {detection_counts}")
        logger.info(f"Series found: {series_counts}")
        logger.info(f"Explicit transoms: {explicit_count}")
        
        return {
            'success': True,
            'results': transom_results,
            'summary': summary
        }
        
    except Exception as e:
        logger.error(f"Error processing ELE transoms: {e}")
        return {
            'success': False,
            'error': str(e),
            'results': []
        }