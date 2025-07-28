#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import pdfplumber
import pandas as pd
import os
import json
import sys
import tempfile
from typing import Dict, List

class PDFExtractorWeb:
    def __init__(self):
        self.reference_code_data = []
        self.glass_data = []
        self.product_info = []
        
    def extract_data_from_file(self, file_path: str, start_page: int = 3) -> Dict:
        """Extract data from PDF file using the original logic"""
        self.reference_code_data = []
        self.glass_data = []
        self.product_info = []
        
        try:
            with pdfplumber.open(file_path) as pdf:
                start_idx = start_page - 1
                
                if start_idx >= len(pdf.pages):
                    return {"error": f"หน้าที่ {start_page} ไม่มีในไฟล์ PDF (มีทั้งหมด {len(pdf.pages)} หน้า)"}
                
                # Process each page from start_page
                for i in range(start_idx, len(pdf.pages)):
                    page = pdf.pages[i]
                    tables = page.extract_tables()
                    
                    if tables:
                        for j, table in enumerate(tables):
                            # Extract product information
                            product_info = self._extract_product_info(table, i+1)
                            self.product_info.extend(product_info)
                            
                            # Extract reference and glass data
                            self._process_structured_table(table, i+1, j+1)
                
                return self._format_output()
                
        except Exception as e:
            return {"error": f"เกิดข้อผิดพลาดในการอ่าน PDF: {str(e)}"}
    
    def _process_structured_table(self, table: List, page_num: int, table_num: int):
        """Process table based on known structure from debug output"""
        if not table or len(table) < 6:
            return
        
        # Look for data rows - typically rows 5 and onwards contain actual data
        # Based on debug output, data rows are around index 5-8
        data_rows = []
        
        for row_idx, row in enumerate(table):
            if row_idx < 5:  # Skip header rows
                continue
            
            # Check if this is a data row (has meaningful content)
            if (row and len(row) >= 17 and 
                row[0] and str(row[0]).strip().isdigit() and  # First column is number
                row[1] and str(row[1]).strip()):  # Second column has reference code
                
                data_rows.append((row_idx, row))
        
        # Extract Reference Code and GLASS data from each data row
        for row_idx, row in data_rows:
            try:
                self._extract_row_data(row, row_idx, page_num)
            except Exception as e:
                pass  # Skip errors in web version
    
    def _extract_row_data(self, row: List, row_idx: int, page_num: int):
        """Extract Reference Code and GLASS data from a single row with intelligent pattern detection"""
        
        # Extract Reference Code data (same as original)
        ref_data = {
            'page': page_num,
            'row': row_idx,
            'No': str(row[0]).strip() if row[0] else '',
            'Reference_Code': str(row[1]).strip() if row[1] else '',
            'Wo': str(row[2]).strip() if len(row) > 2 and row[2] else '',
            'Ho': str(row[3]).strip() if len(row) > 3 and row[3] else '',
            'Name': str(row[4]).strip() if len(row) > 4 and row[4] else '',
            'AL': str(row[5]).strip() if len(row) > 5 and row[5] else '',
            'GLS': str(row[6]).strip() if len(row) > 6 and row[6] else '',
            'Width': str(row[7]).strip() if len(row) > 7 and row[7] else '',
            'Height': str(row[8]).strip() if len(row) > 8 and row[8] else '',
            'S_Spec': str(row[9]).strip() if len(row) > 9 and row[9] else '',
            'Order_Qty': str(row[11]).strip() if len(row) > 11 and row[11] else ''
        }
        
        # Only add if we have meaningful data
        if ref_data['No'] and ref_data['Reference_Code']:
            self.reference_code_data.append(ref_data)
        
        # Smart GLASS data extraction
        self._extract_glass_smart(row, row_idx, page_num)

    def _extract_product_info(self, table: List, page_num: int):
        """Extract Product name and Order Qty (sets) information"""
        product_info = []
        
        for row_idx, row in enumerate(table):
            if row and len(row) > 10:
                # Look for Product name pattern
                for i, cell in enumerate(row):
                    if cell and str(cell).strip() == 'Product name':
                        # Look for product code in the same row or next row
                        product_name = ''
                        order_qty = ''
                        
                        # Check same row for product code (usually a few columns after)
                        for j in range(i + 1, min(len(row), i + 10)):
                                cell_val = str(row[j]).strip() if row[j] else ''
                                if cell_val:
                                # This could be product code
                                    product_name = str(row[j]).strip()
                                    break
                        
                        # Look for Order Qty (sets) in the same table
                        for order_row_idx, order_row in enumerate(table):
                            if order_row:
                                for k, order_cell in enumerate(order_row):
                                    if order_cell and 'Order Qty' in str(order_cell):
                                        # Look for quantity value in nearby cells or next row
                                        # Check same row first
                                        for qty_idx in range(k - 2, min(len(order_row), k + 3)):
                                            if (qty_idx >= 0 and order_row[qty_idx] and 
                                                str(order_row[qty_idx]).strip().isdigit()):
                                                order_qty = str(order_row[qty_idx]).strip()
                                                break
                                        
                                        # If not found in same row, check next row
                                        if not order_qty and order_row_idx + 1 < len(table):
                                            next_row = table[order_row_idx + 1]
                                            if next_row and len(next_row) > k:
                                                for qty_idx in range(max(0, k - 2), min(len(next_row), k + 3)):
                                                    if (next_row[qty_idx] and 
                                                        str(next_row[qty_idx]).strip().isdigit()):
                                                        order_qty = str(next_row[qty_idx]).strip()
                                                        break
                                        break
                        
                        if product_name:
                            product_info.append({
                                'page': page_num,
                                'product_name': product_name,
                                'order_qty_sets': order_qty,
                                'message': f"Product name {product_name} มี Order Qty (sets) {order_qty if order_qty else 'ไม่พบข้อมูล'}"
                            })
                        break
        
        return product_info
    
    def _extract_glass_smart(self, row: List, row_idx: int, page_num: int):
        """Extract GLASS data using intelligent pattern recognition"""
        
        # Look for 4-digit numbers that could be GW/GH (glass dimensions)
        # and single digits that could be Qty
        potential_glass_data = []
        
        # Start looking after basic reference data (column 12+)
        start_col = 12
        
        for i in range(start_col, len(row)):
            if row[i] and str(row[i]).strip():
                value = str(row[i]).strip()
                
                # Check if this could be glass dimension (3-4 digit number)
                if value.isdigit() and len(value) >= 3:
                    potential_glass_data.append({
                        'index': i,
                        'value': value,
                        'type': 'dimension' if len(value) >= 3 else 'qty'
                    })
                # Check if this could be quantity (1-2 digit number)
                elif value.isdigit() and len(value) <= 2:
                    potential_glass_data.append({
                        'index': i,
                        'value': value,
                        'type': 'qty'
                    })
        
        # Group potential glass data into sets
        # Pattern: usually GW (4-digit), GH (4-digit), Qty (1-2 digit)
        glass_sets = self._group_glass_data(potential_glass_data)
        
        # Create glass entries
        for set_num, glass_set in enumerate(glass_sets, 1):
            if glass_set:  # Only if we have data
                glass_data = {
                    'page': page_num,
                    'row': row_idx,
                    'ref_no': str(row[0]).strip() if row[0] else '',
                    'ref_code': str(row[1]).strip() if row[1] else '',
                    'glass_set': set_num,
                    'GW': glass_set.get('gw', ''),
                    'GH': glass_set.get('gh', ''),
                    'Qty': glass_set.get('qty', '')
                }
                
                
                # Only add if we have at least one meaningful value
                if glass_data['GW'] or glass_data['GH'] or glass_data['Qty']:
                    self.glass_data.append(glass_data)
    
    def _group_glass_data(self, potential_data):
        """Group potential glass data into logical sets (GW, GH, Qty)"""
        if not potential_data:
            return []
        
        glass_sets = []
        current_set = {}
        
        i = 0
        while i < len(potential_data):
            item = potential_data[i]
            
            # Look for pattern: dimension, dimension, qty
            # or just dimensions without qty
            if item['type'] == 'dimension':
                if not current_set:
                    # Start new set with first dimension (likely GW)
                    current_set['gw'] = item['value']
                elif 'gw' in current_set and 'gh' not in current_set:
                    # Second dimension (likely GH)
                    current_set['gh'] = item['value']
                    
                    # Look ahead for quantity
                    if (i + 1 < len(potential_data) and 
                        potential_data[i + 1]['type'] == 'qty'):
                        current_set['qty'] = potential_data[i + 1]['value']
                        i += 1  # Skip next item as we used it
                    
                    # Complete this set
                    glass_sets.append(current_set)
                    current_set = {}
                else:
                    # Start new set if current is full
                    if current_set:
                        glass_sets.append(current_set)
                    current_set = {'gw': item['value']}
            
            elif item['type'] == 'qty':
                if current_set and 'gh' in current_set:
                    # Add qty to current set
                    current_set['qty'] = item['value']
                    glass_sets.append(current_set)
                    current_set = {}
                elif not current_set:
                    # Standalone qty - might be for previous incomplete set
                    if glass_sets and 'qty' not in glass_sets[-1]:
                        glass_sets[-1]['qty'] = item['value']
            
            i += 1
        
        # Add any remaining set
        if current_set:
            glass_sets.append(current_set)
        
        return glass_sets
    
    def _format_output(self) -> Dict:
        """Format the extracted data"""
        # Create product info messages
        product_messages = []
        for info in self.product_info:
            product_messages.append(info['message'])
        
        return {
            'reference_code': self.reference_code_data,
            'glass_data': self.glass_data,
            'product_info': self.product_info,
            'product_messages': product_messages,
            'total_references': len(self.reference_code_data),
            'total_glass': len(self.glass_data)
        }

def generate_text_output(glass_data):
    """Generate text format output in the new simplified format: RefCode GW * GH = Qty
    Only include entries with complete GLASS data (RefCode, GW, GH, and Qty)
    Remove leading zeros from GW and GH values"""
    content = ""
    
    def remove_leading_zeros(value):
        """Remove leading zeros from numeric strings"""
        if not value or not str(value).strip():
            return value
        
        # Convert to string and strip whitespace
        val_str = str(value).strip()
        
        # If it's all digits, remove leading zeros but keep at least one digit
        if val_str.isdigit():
            return str(int(val_str))
        
        return val_str
    
    if glass_data:
        # Process each glass data entry - only include complete entries
        for glass_item in glass_data:
            ref_code = glass_item.get('ref_code', '').strip()
            gw = glass_item.get('GW', '').strip()
            gh = glass_item.get('GH', '').strip()
            qty = glass_item.get('Qty', '').strip()
            
            # Only create entry if we have ALL required data (RefCode, GW, GH, Qty)
            if ref_code and gw and gh and qty:
                # Remove leading zeros from GW and GH
                gw_clean = remove_leading_zeros(gw)
                gh_clean = remove_leading_zeros(gh)
                
                content += f"{ref_code} {gw_clean} * {gh_clean} = {qty}\n"
    
    # If no complete glass data found, show appropriate message
    if not content:
        content = "ไม่พบข้อมูล GLASS ที่สมบูรณ์\n"
    
    return content

def save_results_to_files(result_data, output_folder='outputs'):
    """Save results to TXT and JSON files"""
    try:
        os.makedirs(output_folder, exist_ok=True)
        
        # Save TXT file
        txt_content = generate_text_output(result_data.get('glass_data', []))
        txt_file = os.path.join(output_folder, 'pdf_results.txt')
        with open(txt_file, 'w', encoding='utf-8') as f:
            f.write(txt_content)
        
        # Save JSON file
        json_file = os.path.join(output_folder, 'pdf_results.json')
        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(result_data, f, ensure_ascii=False, indent=2)
        
        return True
    except Exception as e:
        print(f"Error saving results: {e}", file=sys.stderr)
        return False

def main():
    """Main function for command line usage"""
    if len(sys.argv) < 4:
        print("Usage: python main3.py <pdf_file_path> <start_page> <job_id>", file=sys.stderr)
        sys.exit(1)
    
    pdf_file_path = sys.argv[1]
    start_page = int(sys.argv[2])
    job_id = sys.argv[3]
    
    # Check if PDF file exists
    if not os.path.exists(pdf_file_path):
        result = {"error": f"ไม่พบไฟล์ PDF: {pdf_file_path}"}
        print(json.dumps(result, ensure_ascii=False))
        sys.exit(1)
    
    # Initialize extractor and process PDF
    extractor = PDFExtractorWeb()
    
    try:
        result = extractor.extract_data_from_file(pdf_file_path, start_page)
        
        # Save results to files if processing was successful
        if 'error' not in result:
            save_results_to_files(result)
        
        # Output JSON result to stdout for server.py to parse
        print(json.dumps(result, ensure_ascii=False))
        
    except Exception as e:
        error_result = {"error": f"เกิดข้อผิดพลาดที่ไม่คาดคิด: {str(e)}"}
        print(json.dumps(error_result, ensure_ascii=False))
        sys.exit(1)

if __name__ == '__main__':
    main()