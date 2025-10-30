# use of main5.py

# door_direction_full.py - ปรับปรุงการแยกข้อมูล direction ให้ถูกต้อง
import pdfplumber
import re
import logging

logger = logging.getLogger(__name__)

class DoorDirectionProcessor:
    """Processor for extracting and comparing door AND window directions from Site Survey and ELE PDFs"""
    
    def __init__(self):
        self.direction_patterns = {
            'right': ['R', 'RIGHT', 'right', 'r', 'RH', 'RIGHT HAND'],
            'left': ['L', 'LEFT', 'left', 'l', 'LH', 'LEFT HAND']
        }
        # Add exclusion patterns for descriptions to skip
        self.skip_descriptions = [
            'INSECT SCREEN FOR SASH PRODUCT',
            'INSECT SCREEN',
            'SCREEN FOR SASH'
        ]
    
    def extract_site_survey_directions(self, file_path):
        """Extract door AND WINDOW directions from Site Survey PDF - FIX: ดึงจาก Product type column"""
        directions = []
        
        try:
            with pdfplumber.open(file_path) as pdf:
                logger.info(f"Processing site survey PDF with {len(pdf.pages)} pages")
                
                for page_num, page in enumerate(pdf.pages, 1):
                    tables = page.extract_tables()
                    if not tables:
                        continue
                    
                    for table in tables:
                        if not table:
                            continue
                        
                        # Process each row in the table
                        for row in table:
                            if not row or len(row) < 3:  # ต้องมีอย่างน้อย 3 columns
                                continue
                            
                            # Check if this row contains a valid reference code
                            ref = str(row[0]).strip() if row[0] else ""
                            if not re.match(r'^[WDA]\w*\d*(\.\d+)?$', ref):
                                continue
                            
                            # **FIX: ดึง direction จาก Product type (column 2) แทนครั้งนี้**
                            product_type = str(row[2]).strip() if len(row) > 2 and row[2] else ""
                            
                            logger.info(f"Site Survey - Processing {ref}: Product type = '{product_type}'")
                            
                            # Extract direction from product type
                            direction = self._extract_direction_from_product_type(product_type)
                            
                            # ถ้าไม่เจอใน product type ให้หาจากคอลัมน์อื่น
                            if not direction:
                                for col_idx in range(len(row)):
                                    if row[col_idx] and col_idx != 2:  # ข้าม product type column ที่เคยแล้ว
                                        col_text = str(row[col_idx]).strip()
                                        direction = self._extract_direction_from_text(col_text)
                                        if direction:
                                            logger.info(f"Found direction '{direction}' in column {col_idx}: '{col_text}'")
                                            break
                            
                            # เพิ่มผลลัพธ์การ (รวมที่ไม่มี direction)
                            directions.append({
                                'Ref': ref,
                                'Site_Direction': direction if direction else '-',
                                'Site_Product_Type': product_type,
                                'Page': page_num,
                                'Type': 'Door' if ref.startswith(('D', 'A')) else 'Window'
                            })
                            
                            if direction:
                                logger.info(f"Site direction found: {ref} ({directions[-1]['Type']}) = {direction}")
                            else:
                                logger.info(f"Site item without direction: {ref} ({directions[-1]['Type']})")
        
        except Exception as e:
            logger.error(f"Error extracting site survey directions: {e}")
        
        logger.info(f"Site survey direction extraction completed: {len(directions)} items")
        return directions
    
    def _extract_direction_from_product_type(self, product_type):
        """ฟังก์ชันพิเศษสำหรับ extract direction จาก Product type"""
        if not product_type:
            return None
        
        text_upper = product_type.upper().strip()
        
        # Pattern หลักสำหรับ Product type
        # 1. หา LEFT/RIGHT words ก่อน (มีความชัดเจนมากที่สุด)
        if re.search(r'\bLEFT\b', text_upper):
            return 'L'
        elif re.search(r'\bRIGHT\b', text_upper):
            return 'R'
        
        # 2. หา LEFT HAND/RIGHT HAND patterns
        if re.search(r'LEFT\s*HAND', text_upper) or 'LH' in text_upper:
            return 'L'
        elif re.search(r'RIGHT\s*HAND', text_upper) or 'RH' in text_upper:
            return 'R'
        
        # 3. ลองหา L หรือ R ที่อยู่ท้ายสุดของข้อความ
        if text_upper.endswith(' L'):
            return 'L'
        elif text_upper.endswith(' R'):
            return 'R'
        
        # 4. หา L หรือ R ที่เป็นคำแยกต่างหากของส่วนอื่น
        # ใช้ negative lookbehind และ lookahead เพื่อหลีกเลี่ยงการจับ R ใน "DOOR"
        if re.search(r'(?<![A-Z])\bL\b(?![A-Z])', text_upper):
            return 'L'
        elif re.search(r'(?<![A-Z])\bR\b(?![A-Z])', text_upper):
            return 'R'
        
        return None
    
    def extract_ele_directions(self, file_path):
        """Extract door AND window directions from ELE PDF - ดึงจาก Description"""
        directions = []
        
        try:
            with pdfplumber.open(file_path) as pdf:
                logger.info(f"Processing ELE PDF with {len(pdf.pages)} pages")
                
                for page_num, page in enumerate(pdf.pages, 1):
                    text = page.extract_text() or ""
                    tables = page.extract_tables()
                    
                    # Find reference codes
                    refs = re.findall(r'\b(?:[WD]A?\d+(?:\.\d+)?|ADD)\b', text)
                    
                    if not refs:
                        continue
                    
                    # Extract description from text
                    description = self._extract_description_from_page(text)
                    
                    # **NEW: Check if description should be skipped**
                    if self._should_skip_description(description):
                        logger.info(f"ELE Page {page_num} - Skipping INSECT SCREEN description: '{description}'")
                        continue
                    
                    logger.info(f"ELE Page {page_num} - Description: '{description}'")
                    
                    for ref in refs:
                        # Skip duplicates on same page
                        if any(item['Ref'] == ref and item['Page'] == page_num for item in directions):
                            continue
                        
                        # **FIX: Extract direction from description**
                        direction = self._extract_direction_from_text(description)
                        
                        logger.info(f"ELE - Processing {ref}: Description = '{description}', Direction = '{direction}'")
                        
                        directions.append({
                            'Ref': ref,
                            'ELE_Direction': direction if direction else '-',
                            'ELE_Description': description,
                            'Page': page_num,
                            'Type': 'Door' if ref.startswith(('D', 'A')) else 'Window'
                        })
                        
                        if direction:
                            logger.info(f"ELE direction found: {ref} ({directions[-1]['Type']}) = {direction}")
                        else:
                            logger.info(f"ELE item without direction: {ref} ({directions[-1]['Type']})")
        
        except Exception as e:
            logger.error(f"Error extracting ELE directions: {e}")
        
        logger.info(f"ELE direction extraction completed: {len(directions)} items")
        return directions
    
    def _should_skip_description(self, description):
        """Check if description should be skipped (e.g., INSECT SCREEN products)"""
        if not description:
            return False
        
        description_upper = description.upper().strip()
        
        for skip_pattern in self.skip_descriptions:
            if skip_pattern.upper() in description_upper:
                return True
        
        return False
    
    def _extract_direction_from_text(self, text):
        """Extract direction (L/R) from text - ENHANCED"""
        if not text:
            return None
        
        text_upper = text.upper().strip()
        
        # Priority 1: Hand-based patterns (most specific)
        if re.search(r'\bLEFT\s*HAND\b', text_upper) or re.search(r'\bLH\b', text_upper):
            return 'L'
        elif re.search(r'\bRIGHT\s*HAND\b', text_upper) or re.search(r'\bRH\b', text_upper):
            return 'R'
        
        # Priority 2: LEFT/RIGHT words (clear and unambiguous)
        if re.search(r'\bLEFT\b', text_upper):
            return 'L'
        elif re.search(r'\bRIGHT\b', text_upper):
            return 'R'
        
        # Priority 3: Direction at end of string
        if text_upper.endswith(' L'):
            return 'L'
        elif text_upper.endswith(' R'):
            return 'R'
        
        # Priority 4: L/R in product codes like LWE70, RWE70 etc.
        if re.search(r'\bL[A-Z]*\d', text_upper):  # L followed by optional letters then digits
            return 'L'
        elif re.search(r'\bR[A-Z]*\d', text_upper):  # R followed by optional letters then digits
            return 'R'
        
        # Priority 5: Single letter patterns (most restrictive - avoid false matches)
        # Only match L/R that are standalone and not part of other words
        if re.search(r'(?<![A-Z])\bL\b(?![A-Z])', text_upper):
            return 'L'
        elif re.search(r'(?<![A-Z])\bR\b(?![A-Z])', text_upper):
            return 'R'
        
        return None
    
    def _extract_description_from_page(self, text):
        """Extract description from page text - ปรับปรุงให้ดีขึ้น"""
        lines = text.splitlines()
        
        # หา Description line
        for line in lines:
            if "Description" in line:
                # Remove "Description" and clean up
                description = line.replace("Description", "").strip()
                # Remove common prefixes
                description = re.sub(r'^[:\-\s]+', '', description)
                
                # ทำความสะอาดเพิ่มเติม
                description = description.replace("Customer approve", "").strip()
                description = description.replace("customer approve", "").strip()
                
                logger.info(f"Found description: '{description}'")
                return description
        
        return ""
    
    def compare_directions(self, site_directions, ele_directions):
        """Compare door AND window directions between Site Survey and ELE"""
        results = []
        
        # Create lookup dictionaries
        site_lookup = {item['Ref']: item for item in site_directions}
        ele_lookup = {item['Ref']: item for item in ele_directions}
        
        # Get all unique references
        all_refs = set(site_lookup.keys()) | set(ele_lookup.keys())
        
        for ref in sorted(all_refs):
            site_item = site_lookup.get(ref, {})
            ele_item = ele_lookup.get(ref, {})
            
            site_direction = site_item.get('Site_Direction', '-')
            ele_direction = ele_item.get('ELE_Direction', '-')
            item_type = site_item.get('Type') or ele_item.get('Type', 'Unknown')
            
            logger.info(f"Comparing {ref}: Site='{site_direction}', ELE='{ele_direction}', Type='{item_type}'")
            
            # Determine match status
            if site_direction == '-' and ele_direction == '-':
                if item_type == 'Window':
                    status = "⚠️ No Direction Info"
                    notes = "Window may not require direction specification"
                else:
                    status = "⚠️ No Direction Info"
                    notes = f"No direction information found for this {item_type.lower()}"
            elif site_direction == '-':
                if item_type == 'Window':
                    status = "ℹ️ Window Direction in ELE Only"
                    notes = f"Window direction found in ELE ({ele_direction}) but not in Site Survey"
                else:
                    status = "ℹ️ Missing in Site"
                    notes = f"Direction found in ELE ({ele_direction}) but not in Site Survey"
            elif ele_direction == '-':
                status = "ℹ️ Missing in ELE"
                notes = f"Direction found in Site Survey ({site_direction}) but not in ELE"
            elif site_direction == ele_direction:
                status = "✅ Direction Match"
                notes = f"{item_type} direction matches: {site_direction}"
            else:
                status = "❌ Direction Mismatch"
                notes = f"{item_type} direction mismatch - Site: {site_direction}, ELE: {ele_direction}"
            
            result = {
                'Ref': ref,
                'Site_Direction': site_direction,
                'ELE_Direction': ele_direction,
                'Status': status,
                'Notes': notes,
                'Type': item_type,
                'Site_Product_Type': site_item.get('Site_Product_Type', '-'),
                'ELE_Description': ele_item.get('ELE_Description', '-')
            }
            
            results.append(result)
        
        return results

def process_door_directions(site_path, ele_path):
    """Main function to process door AND window directions"""
    processor = DoorDirectionProcessor()
    
    try:
        logger.info("=== STARTING ENHANCED DOOR & WINDOW DIRECTION PROCESSING ===")
        
        # Extract directions from both documents
        site_directions = processor.extract_site_survey_directions(site_path)
        ele_directions = processor.extract_ele_directions(ele_path)
        
        # Log summary by type
        site_doors = len([d for d in site_directions if d.get('Type') == 'Door'])
        site_windows = len([d for d in site_directions if d.get('Type') == 'Window'])
        ele_doors = len([d for d in ele_directions if d.get('Type') == 'Door'])
        ele_windows = len([d for d in ele_directions if d.get('Type') == 'Window'])
        
        logger.info(f"Site Survey: {site_doors} doors, {site_windows} windows")
        logger.info(f"ELE: {ele_doors} doors, {ele_windows} windows")
        
        # Compare directions
        comparison_results = processor.compare_directions(site_directions, ele_directions)
        
        # Calculate summary statistics
        total_items = len(comparison_results)
        matches = len([r for r in comparison_results if '✅' in r['Status']])
        mismatches = len([r for r in comparison_results if '❌' in r['Status']])
        missing_data = len([r for r in comparison_results if 'Missing' in r['Status']])
        no_direction_info = len([r for r in comparison_results if 'No Direction Info' in r['Status']])
        
        # Count by type
        door_results = [r for r in comparison_results if r.get('Type') == 'Door']
        window_results = [r for r in comparison_results if r.get('Type') == 'Window']
        
        logger.info(f"Enhanced direction processing completed:")
        logger.info(f"  Total items: {total_items} ({len(door_results)} doors, {len(window_results)} windows)")
        logger.info(f"  Matches: {matches}")
        logger.info(f"  Mismatches: {mismatches}")
        logger.info(f"  Missing data: {missing_data}")
        logger.info(f"  No direction info: {no_direction_info}")
        
        return {
            'success': True,
            'results': comparison_results,
            'summary': {
                'total_items': total_items,
                'matches': matches,
                'mismatches': mismatches,
                'missing_data': missing_data,
                'no_direction_info': no_direction_info,
                'match_rate': round((matches / total_items * 100), 1) if total_items > 0 else 0,
                'door_count': len(door_results),
                'window_count': len(window_results)
            },
            'site_directions': site_directions,
            'ele_directions': ele_directions
        }
    
    except Exception as e:
        logger.error(f"Direction processing failed: {e}")
        return {
            'success': False,
            'error': str(e),
            'results': []
        }

if __name__ == "__main__":
    # Test the module
    logging.basicConfig(level=logging.INFO)