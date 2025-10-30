# use of main5.py

# color_comparison_app.py - Complete Fixed Color Comparison Module with Color Code Matching
import pdfplumber
import re

# No-op logger to suppress logs and keep functions intact
class _NoopLogger:
    def info(self, *args, **kwargs): pass
    def error(self, *args, **kwargs): pass
    def warning(self, *args, **kwargs): pass

logger = _NoopLogger()

class ColorExtractor:
    """Class to extract color information from PDFs"""
    
    @staticmethod
    def extract_site_colors(file_path):
        """Extract color information from site survey PDF - FIXED VERSION"""
        results = []
        
        try:
            with pdfplumber.open(file_path) as pdf:
                for page_num, page in enumerate(pdf.pages, 1):
                    text = page.extract_text() or ""
                    tables = page.extract_tables()
                    
                    # Find reference codes on this page
                    refs = re.findall(r'\b(?:[WD]A?\d+(?:\.\d+)?|ADD)\b', text)
                    
                    if not refs:
                        continue
                    
                    # Process each table using the FIXED method
                    if tables:
                        table_results = ColorExtractor._process_site_tables_fixed(tables, text, page_num, refs)
                        results.extend(table_results)
            
            return results
                
        except Exception:
            return []
    
    @staticmethod
    def _process_site_tables_fixed(tables, text, page_num, refs):
        """Fixed table processing for site survey - targets the exact table structure"""
        results = []
        
        # First, check if there's a Giesta series section
        giesta_color_from_section = ColorExtractor._extract_giesta_section_color(text)
        
        for table_idx, table in enumerate(tables):
            if not table or len(table) < 2:
                continue
            
            # Look for the main data table with the expected structure
            header_row = table[0] if table else []
            
            if len(header_row) < 6:
                continue
            
            # Find the Color column - should be around position 4-6
            color_column = None
            ref_column = None
            series_column = None
            
            for col_idx, header in enumerate(header_row):
                if header:
                    header_text = str(header).lower().strip()
                    
                    if 'ref' in header_text:
                        ref_column = col_idx
                    elif 'series' in header_text:
                        series_column = col_idx
                    elif 'color' in header_text or 'colour' in header_text:
                        color_column = col_idx
            
            if color_column is None:
                if len(header_row) >= 6:
                    color_column = 6
            
            if ref_column is None:
                ref_column = 0
            
            if series_column is None:
                series_column = 1
            
            for row_idx, row in enumerate(table[1:], 1):
                if not row or len(row) <= color_column:
                    continue
                
                # Get reference
                row_ref = None
                if ref_column < len(row) and row[ref_column]:
                    row_ref = str(row[ref_column]).strip()
                    if not re.match(r'^(?:[WD]A?\d+(?:\.\d+)?|ADD)$', row_ref):
                        row_ref = None
                
                if not row_ref and refs and row_idx <= len(refs):
                    row_ref = refs[row_idx - 1]
                
                if not row_ref or row_ref not in refs:
                    continue
                
                # Get series
                series_text = ""
                if series_column < len(row) and row[series_column]:
                    series_text = str(row[series_column]).strip()
                
                is_giesta = 'GIESTA' in series_text.upper() or 'Giesta' in series_text
                
                if is_giesta:
                    giesta_section_valid = (giesta_color_from_section and 
                                        giesta_color_from_section.get('name', '').lower().strip() not in 
                                        ['select..', 'select.', 'select', 'elect..', 'elect.', 'elect', ''])
                    
                    if giesta_section_valid:
                        results.append({
                            'Ref': row_ref,
                            'Color': giesta_color_from_section['original_text'],
                            'Color_Code': giesta_color_from_section['code'],
                            'Color_Type': 'giesta',
                            'Original_Text': f"For giesta series: {giesta_color_from_section['original_text']}",
                            'Source': 'site',
                            'Page': page_num,
                            'Method': 'giesta_series_extraction'
                        })
                    else:
                        color_text = ""
                        if color_column < len(row) and row[color_column]:
                            color_text = str(row[color_column]).strip()
                        
                        if color_text and color_text not in ['-', '', 'None', 'Select..', 'Select.', 'Select']:
                            parsed_color = ColorExtractor._parse_color_text(color_text)
                            
                            if parsed_color:
                                results.append({
                                    'Ref': row_ref,
                                    'Color': parsed_color['display'],
                                    'Color_Code': parsed_color['code'],
                                    'Color_Type': 'giesta',
                                    'Original_Text': color_text,
                                    'Source': 'site',
                                    'Page': page_num,
                                    'Method': 'giesta_table_extraction'
                                })
                            else:
                                results.append({
                                    'Ref': row_ref,
                                    'Color': color_text,
                                    'Color_Code': color_text.upper(),
                                    'Color_Type': 'giesta',
                                    'Original_Text': color_text,
                                    'Source': 'site',
                                    'Page': page_num,
                                    'Method': 'giesta_table_extraction_raw'
                                })
                        else:
                            results.append({
                                'Ref': row_ref,
                                'Color': '-',
                                'Color_Code': '-',
                                'Color_Type': 'giesta',
                                'Original_Text': f'No color found in column {color_column}',
                                'Source': 'site',
                                'Page': page_num,
                                'Method': 'giesta_table_extraction_default'
                            })
                else:
                    color_text = ""
                    if color_column < len(row) and row[color_column]:
                        color_text = str(row[color_column]).strip()
                    
                    if color_text and color_text not in ['-', '', 'None', 'Select..']:
                        parsed_color = ColorExtractor._parse_color_text(color_text)
                        
                        if parsed_color:
                            results.append({
                                'Ref': row_ref,
                                'Color': parsed_color['display'],
                                'Color_Code': parsed_color['code'],
                                'Color_Type': parsed_color['type'],
                                'Original_Text': color_text,
                                'Source': 'site',
                                'Page': page_num,
                                'Method': 'table_extraction'
                            })
                        else:
                            results.append({
                                'Ref': row_ref,
                                'Color': color_text,
                                'Color_Code': color_text.upper(),
                                'Color_Type': 'aluminum',
                                'Original_Text': color_text,
                                'Source': 'site',
                                'Page': page_num,
                                'Method': 'table_extraction_raw'
                            })
                    else:
                        results.append({
                            'Ref': row_ref,
                            'Color': '-',
                            'Color_Code': '-',
                            'Color_Type': 'aluminum',
                            'Original_Text': f'No color found in column {color_column}',
                            'Source': 'site',
                            'Page': page_num,
                            'Method': 'table_extraction_default'
                        })
        
        return results

    @staticmethod
    def _parse_color_text(color_text):
        """Parse color text like '[K]Shine Grey' to extract code and name"""
        if not color_text:
            return None
        
        color_text = color_text.strip()
        
        bracket_match = re.search(r'\[([A-Z])\]\s*(.+)', color_text)
        if bracket_match:
            code = bracket_match.group(1)
            name = bracket_match.group(2).strip()
            
            return {
                'code': code,
                'display': f"[{code}]{name}",
                'type': 'aluminum'
            }
        
        if len(color_text) == 1 and color_text.upper() in ['T', 'P', 'D', 'W', 'K', 'G', 'U', 'B', 'C', 'F', 'H', 'J']:
            code = color_text.upper()
            return {
                'code': code,
                'display': code,
                'type': 'aluminum'
            }
        
        return {
            'code': color_text.upper(),
            'display': color_text,
            'type': 'aluminum'
        }

    @staticmethod
    def _extract_giesta_section_color(text):
        """Extract color from ☐For giesta series section"""
        
        if "giesta" in text.lower():
            giesta_pos = text.lower().find("giesta")
            start = max(0, giesta_pos - 100)
            end = min(len(text), giesta_pos + 200)
            context = text[start:end]
        else:
            context = ""
        
        giesta_patterns = [
            r'☐\s*For\s+giesta\s+series[^☐☑]*?Color\s*:\s*([^☐☑\n\r]+)',
            r'☑\s*For\s+giesta\s+series[^☐☑]*?Color\s*:\s*([^☐☑\n\r]+)',
            r'For\s+giesta\s+series[^☐☑]*?Color\s*:\s*([^\n\r]+)',
            r'☐\s*For\s+giesta\s+series.*?Color\s*:\s*\[([A-Z])\]([^\n\r☐☑]+)',
            r'☑\s*For\s+giesta\s+series.*?Color\s*:\s*\[([A-Z])\]([^\n\r☐☑]+)',
            r'For\s+giesta\s+series.*?Color\s*:\s*(.+?)(?=\n|\r|$)',
            r'[☐☑]\s*.*?giesta.*?Color\s*:\s*([^\n\r]+)',
        ]
        
        for pattern in giesta_patterns:
            giesta_match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if giesta_match:
                if len(giesta_match.groups()) >= 2:
                    code = giesta_match.group(1)
                    name = giesta_match.group(2).strip()
                    color_text = f"[{code}]{name}"
                else:
                    color_text = giesta_match.group(1).strip()
                
                color_info = ColorExtractor._parse_giesta_color_text(color_text)
                
                if color_info:
                    return {
                        'code': color_info['code'],
                        'name': color_info['name'],
                        'original_text': color_text
                    }
                break
        
        simple_pattern = r'\[([A-Z])\]([A-Za-z\s]+)'
        simple_matches = re.findall(simple_pattern, text)
        if simple_matches:
            code, name = simple_matches[0]
            color_text = f"[{code}]{name}"
            
            color_info = ColorExtractor._parse_giesta_color_text(color_text)
            if color_info:
                return {
                    'code': color_info['code'],
                    'name': color_info['name'],
                    'original_text': color_text
                }
        
        return None
    
    @staticmethod
    def _parse_giesta_color_text(color_text):
        """Parse color text specifically for Giesta series"""
        if not color_text or color_text == '-':
            return None
        
        color_text = color_text.strip()
        
        code_match = re.search(r'\[([A-Z])\]\s*(.+)', color_text)
        if code_match:
            code = code_match.group(1)
            name = code_match.group(2).strip()
            
            return {
                'code': code,
                'name': name,
                'type': 'giesta'
            }
        
        simple_code_match = re.search(r'^([A-Z])\s*(.+)', color_text)
        if simple_code_match:
            code = simple_code_match.group(1)
            name = simple_code_match.group(2).strip()
            
            return {
                'code': code,
                'name': name,
                'type': 'giesta'
            }
        
        if len(color_text) == 1 and color_text.upper().isalpha():
            code = color_text.upper()
            return {
                'code': code,
                'name': code,
                'type': 'giesta'
            }
        
        return {
            'code': 'UNSPECIFIED',
            'name': color_text,
            'type': 'giesta'
        }
    
    @staticmethod
    def extract_ele_colors(file_path):
        """Extract color information from ELE PDF - get from AL column directly"""
        results = []
        
        try:
            with pdfplumber.open(file_path) as pdf:
                for page_num, page in enumerate(pdf.pages, 1):
                    text = page.extract_text() or ""
                    tables = page.extract_tables()
                    
                    refs = re.findall(r'\b(?:[WD]A?\d+(?:\.\d+)?|ADD)\b', text)
                    unique_refs = list(dict.fromkeys(refs))
                    
                    if not unique_refs:
                        continue
                    
                    if tables:
                        table_colors = ColorExtractor._extract_colors_from_ele_tables(tables, page_num, unique_refs)
                        results.extend(table_colors)
                    
                    if not tables:
                        text_colors = ColorExtractor._extract_colors_from_ele_text(text, page_num, unique_refs)
                        results.extend(text_colors)
            
            return results
                
        except Exception:
            return []
    
    @staticmethod
    def _extract_colors_from_ele_tables(tables, page_num, refs):
        """Extract color information from ELE tables using multiple column approach"""
        results = []
        
        for table_idx, table in enumerate(tables):
            if not table:
                continue
            
            header_row = table[0] if table else []
            al_column = None
            ref_column = None
            
            for col_idx, header in enumerate(header_row):
                if header:
                    header_text = str(header).strip().upper()
                    if header_text == 'AL':
                        al_column = col_idx
                    elif 'REFERENCE' in header_text or header_text == 'REFERENCE CODE':
                        ref_column = col_idx
            
            if al_column is None:
                possible_al_positions = [5, 6]
                for pos in possible_al_positions:
                    if len(header_row) > pos:
                        al_column = pos
                        break
                
                if al_column is None:
                    continue
            
            for row_idx, row in enumerate(table[1:], 1):
                if not row:
                    continue
                
                row_ref = None
                if ref_column is not None and ref_column < len(row) and row[ref_column]:
                    row_ref = str(row[ref_column]).strip()
                    if re.match(r'^(?:[WD]A?\d+(?:\.\d+)?|ADD)$', row_ref):
                        pass
                    else:
                        row_ref = None
                
                if not row_ref:
                    for col_idx in range(min(3, len(row))):
                        if row[col_idx]:
                            cell_text = str(row[col_idx]).strip()
                            if re.match(r'^(?:[WD]A?\d+(?:\.\d+)?|ADD)$', cell_text):
                                row_ref = cell_text
                                break
                
                if not row_ref or row_ref not in refs:
                    continue
                
                # Try multiple columns to find color
                color_code = None
                color_raw = ""
                columns_to_check = []
                if al_column is not None:
                    for offset in [0, 1, -1, 2, -2]:
                        check_col = al_column + offset
                        if 0 <= check_col < len(row):
                            columns_to_check.append(check_col)
                common_color_positions = [4, 5, 6, 7, 8]
                for pos in common_color_positions:
                    if pos < len(row) and pos not in columns_to_check:
                        columns_to_check.append(pos)
                
                for check_col in columns_to_check:
                    if check_col >= len(row):
                        continue
                        
                    current_color_raw = ""
                    if row[check_col]:
                        current_color_raw = str(row[check_col]).strip()
                    
                    if current_color_raw and current_color_raw.upper() not in ['AL', 'SELECT..', 'SELECT', '', '-', 'NONE']:
                        color_clean = current_color_raw.upper().strip()
                        if len(color_clean) <= 2 and color_clean.isalpha():
                            color_code = color_clean
                            color_raw = current_color_raw
                            break

                if not color_code:
                    color_code = "-"
                    color_raw = "Not found"
                
                results.append({
                    'Ref': row_ref,
                    'Color': color_code,
                    'Color_Code': color_code,
                    'Color_Type': 'aluminum',
                    'Original_Text': f"Multi-column search: {color_raw}",
                    'Source': 'ele',
                    'Page': page_num,
                    'Method': 'multi_column_extraction'
                })
        
        return results
    
    @staticmethod
    def _extract_colors_from_ele_text(text, page_num, refs):
        """Extract colors from ELE text using proven search patterns"""
        results = []
        
        colors = ['G', 'J', 'T', 'P', 'D', 'W', 'K', 'U', 'B', 'C', 'F', 'H']
        
        def find_color_in_text(text_content):
            for color in colors:
                if f"AL\t{color}" in text_content or f"AL {color}" in text_content or f"AL\n{color}" in text_content:
                    return color
                if f" {color} " in text_content or f"\n{color}\n" in text_content or f"\t{color}\t" in text_content:
                    return color
            return "-"
        
        found_color = find_color_in_text(text)
        
        for ref in refs:
            results.append({
                'Ref': ref,
                'Color': found_color,
                'Color_Code': found_color,
                'Color_Type': 'aluminum',
                'Original_Text': f"Text search: {found_color}",
                'Source': 'ele',
                'Page': page_num,
                'Method': 'text_pattern_extraction'
            })
        
        return results

class ColorComparator:
    """Class to compare color data between site and ELE"""
    
    @staticmethod
    def compare_colors(site_colors, ele_colors):
        """Compare color data and return results"""
        results = []
        
        site_color_lookup = ColorComparator._group_colors_by_ref(site_colors)
        ele_color_lookup = ColorComparator._group_colors_by_ref(ele_colors)
        
        all_refs = set(site_color_lookup.keys()) | set(ele_color_lookup.keys())
        
        for ref in sorted(all_refs):
            site_color_data = site_color_lookup.get(ref, [])
            ele_color_data = ele_color_lookup.get(ref, [])
            
            site_color_info = ColorComparator._get_primary_color(site_color_data)
            ele_color_info = ColorComparator._get_primary_color(ele_color_data)
            
            site_color = site_color_info.get('Color') if site_color_info else None
            ele_color = ele_color_info.get('Color') if ele_color_info else None
            
            color_match, match_details = ColorComparator._compare_color_values(
                site_color_info, ele_color_info
            )
            
            status, notes = ColorComparator._determine_status_and_notes(
                site_color_info, ele_color_info, color_match, match_details
            )
            
            results.append({
                'Ref': ref,
                'Site_Color': site_color or '-',
                'ELE_Color': ele_color or '-',
                'Site_Color_Code': site_color_info.get('Color_Code', '-') if site_color_info else '-',
                'ELE_Color_Code': ele_color_info.get('Color_Code', '-') if ele_color_info else '-',
                'Status': status,
                'Notes': notes,
                'Site_Details': site_color_data,
                'ELE_Details': ele_color_data
            })
        
        return results
    
    @staticmethod
    def _group_colors_by_ref(color_data):
        lookup = {}
        for item in color_data:
            ref = item.get('Ref')
            if ref not in lookup:
                lookup[ref] = []
            lookup[ref].append(item)
        return lookup
    
    @staticmethod
    def _get_primary_color(color_data):
        if not color_data:
            return None
        
        giesta_series_colors = [item for item in color_data if item.get('Method') == 'giesta_series_extraction']
        if giesta_series_colors:
            return giesta_series_colors[0]

        giesta_table_colors = [item for item in color_data if item.get('Method') in ['giesta_table_extraction', 'giesta_table_extraction_raw']]
        if giesta_table_colors:
            return giesta_table_colors[0]

        table_colors = [item for item in color_data if item.get('Method') == 'table_extraction']
        if table_colors:
            return table_colors[0]
        
        return color_data[0] if color_data else None

    @staticmethod
    def _compare_color_values(site_color_info, ele_color_info):
        """ENHANCED: Compare two color values with color code extraction - FIXES [K]Shine Grey = K"""
        if not site_color_info or not ele_color_info:
            return False, "Missing color information"
        
        site_code = ColorComparator._extract_color_code_enhanced(site_color_info)
        ele_code = ColorComparator._extract_color_code_enhanced(ele_color_info)
        
        if site_code == ele_code:
            return True, f"Color code match: {site_code}"
        
        site_name = site_color_info.get('Color', '').strip().lower()
        ele_name = ele_color_info.get('Color', '').strip().lower()
        
        if site_name and ele_name and site_name == ele_name:
            return True, f"Full color name match: {site_color_info.get('Color').strip()}"
        
        if (('[g]autumn brown' in site_name or 'autumn brown' in site_name) and 
            ('[g]autumn brown' in ele_name or 'autumn brown' in ele_name)):
            return True, f"Giesta Autumn Brown match"
        
        return False, f"Color mismatch: Site [{site_code}] vs ELE [{ele_code}]"

    @staticmethod
    def _extract_color_code_enhanced(color_info):
        """ENHANCED: Extract single letter color code from color info - FIXES [K]Shine Grey"""
        if not color_info:
            return ""
        
        color_code = color_info.get('Color_Code', '').strip().upper()
        
        if len(color_code) == 1 and color_code.isalpha():
            return color_code
        
        color_text = color_info.get('Color', '').strip()
        
        bracket_match = re.search(r'\[([A-Z])\]', color_text)
        if bracket_match:
            extracted_code = bracket_match.group(1)
            return extracted_code
        
        if len(color_text) == 1 and color_text.upper().isalpha():
            return color_text.upper()
        
        bracket_match_code = re.search(r'\[([A-Z])\]', color_code)
        if bracket_match_code:
            return bracket_match_code.group(1)
        
        valid_codes = ['T', 'P', 'D', 'W', 'K', 'G', 'U', 'B', 'C', 'F', 'H', 'J']
        
        for char in color_text.upper():
            if char in valid_codes:
                return char
        
        for char in color_code:
            if char in valid_codes:
                return char
        
        if color_code and color_code[0].isalpha():
            return color_code[0]
        elif color_text and color_text[0].isalpha():
            return color_text[0].upper()
        
        return "-"
    
    @staticmethod
    def _extract_color_code(color_text):
        """DEPRECATED: Use _extract_color_code_enhanced instead"""
        return ColorComparator._extract_color_code_enhanced({'Color': color_text, 'Color_Code': color_text})
    
    @staticmethod
    def _determine_status_and_notes(site_color_info, ele_color_info, color_match, match_details):
        """Determine status and notes for color comparison"""
        
        if not site_color_info and not ele_color_info:
            return "ℹ️ No color data", "No color information found in either document"
        
        if not site_color_info:
            return "❌ Missing in Site", "Color not specified in site survey"
        
        if not ele_color_info:
            return "❌ Missing in ELE", "Color not found in ELE AL column"
        
        if color_match:
            return "✅ Color Match", match_details
        else:
            return "❌ Color Mismatch", match_details

def process_colors(site_file_path, ele_file_path):
    """Main function to process color comparison"""
    try:
        # Extract colors from both files
        site_colors = ColorExtractor.extract_site_colors(site_file_path)
        ele_colors = ColorExtractor.extract_ele_colors(ele_file_path)
        
        # Compare colors
        comparison_results = ColorComparator.compare_colors(site_colors, ele_colors)
        
        return {
            'success': True,
            'results': comparison_results,
            'site_color_data': site_colors,
            'ele_color_data': ele_colors,
            'summary': {
                'total_items': len(comparison_results),
                'color_matches': len([r for r in comparison_results if 'Match' in r['Status']]),
                'color_mismatches': len([r for r in comparison_results if 'Mismatch' in r['Status']]),
                'missing_data': len([r for r in comparison_results if 'Missing' in r['Status']])
            }
        }
        
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'results': []
        }