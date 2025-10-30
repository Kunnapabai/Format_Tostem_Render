#use of site-with-ele.html

# call function from 4 files - transom_comparison.py , door_direction_full.py , color_comparison_full.py , insect_screen_full.py

# ELE vs Site survey check

from flask import Flask, render_template, request, jsonify, send_file
import pdfplumber
import pandas as pd
import re
import io
from datetime import datetime
import os
from werkzeug.utils import secure_filename
import logging
import base64
from openai import OpenAI
import fitz  # PyMuPDF for PDF to image conversion

from dotenv import load_dotenv

# Import sub-panel, insect screen and color processing modules
from insect_screen_full import process_insect_screens
from color_comparison_full import process_colors
from door_direction_full import process_door_directions
from transom_comparison import process_transoms

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
app.config['UPLOAD_FOLDER'] = 'uploads'

load_dotenv()

from werkzeug.serving import WSGIRequestHandler
WSGIRequestHandler.timeout = 1800  # 30 minutes

# Use environment variables
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")

# Initialize OpenAI client
ENABLE_OPENAI = True # ตั้งเป็น False เพื่อปิด API key , True เพื่อเปิด API key

if ENABLE_OPENAI and OPENAI_API_KEY:
    openai_client = OpenAI(
        base_url=OPENAI_BASE_URL,
        api_key=OPENAI_API_KEY,
    )
else:
    openai_client = None


# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create uploads directory if it doesn't exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

import threading

class RevisedTokenConfig:
    """Token config ที่ปรับตามลักษณะเอกสารจริง"""
    
    TOKEN_SETTINGS = {
        'site_survey': {
            'single_ref': 2000,      # 1 ref ต่อหน้า = ใช้น้อย
            'max_tokens': 2000      # เผื่อ case พิเศษ
        },
        'ele': {
            'single_ref': 3000,      # 1 ref
            'few_refs': 3500,       # 2-3 refs  
            'many_refs': 4000,      # 4-5 refs
            'very_many_refs': 4000, # 5+ refs
            'max_tokens': 4500      # เผื่อหน้าที่ซับซ้อนมาก
        }
    }
    
    @staticmethod
    def get_tokens_for_site_survey():
        """Site Survey ใช้ tokens คงที่เพราะมี 1 ref ต่อหน้า"""
        return RevisedTokenConfig.TOKEN_SETTINGS['site_survey']['single_ref']
    
    @staticmethod  
    def get_tokens_for_ele(estimated_refs=1):
        """ELE ปรับ tokens ตามจำนวน refs ที่คาดการณ์"""
        settings = RevisedTokenConfig.TOKEN_SETTINGS['ele']
        
        if estimated_refs <= 1:
            return settings['single_ref']
        elif estimated_refs <= 3:
            return settings['few_refs']
        elif estimated_refs <= 5:
            return settings['many_refs']
        else:
            return settings['very_many_refs']

# การวิเคราะห์หน้า ELE เพื่อประมาณจำนวน refs
class ELEPageAnalyzer:
    
    @staticmethod
    def estimate_ref_count(pdf_path, page_num):
        """ประมาณจำนวน references ในหน้า ELE"""
        try:
            with pdfplumber.open(pdf_path) as pdf:
                page = pdf.pages[page_num]
                text = page.extract_text() or ""
                
                # หา reference patterns
                refs = re.findall(r'\b(?:[WD]A?\d+(?:\.\d+)?|ADD)\b', text)
                unique_refs = list(set(refs))
                
                # วิเคราะห์เพิ่มเติม
                tables = page.extract_tables()
                text_density = len(text.split())
                
                # ปรับจำนวนตามความซับซ้อน
                base_count = len(unique_refs)
                
                # ถ้ามี table เยอะ หรือ text เยอะ อาจมี refs ซ่อนอยู่
                if len(tables) > 2 or text_density > 1000:
                    estimated_count = max(base_count, base_count * 1.2)
                else:
                    estimated_count = base_count
                
                logger.info(f"ELE Page {page_num}: Found {base_count} refs, estimated {estimated_count}")
                
                return int(estimated_count) if estimated_count > 0 else 1
                
        except Exception as e:
            logger.error(f"ELE page analysis failed: {e}")
            return 1  # fallback
        
def calculate_estimated_cost(total_tokens):
    """Calculate estimated cost based on Gemini 2.5 Pro pricing"""
    if total_tokens == 0:
        return 0.0
    
    # Gemini 2.5 Pro pricing (approximate for vision + text)
    # Vision tasks: ~$3.00 per 1M tokens
    cost_per_million_tokens = 3.00
    return (total_tokens / 1_000_000) * cost_per_million_tokens

class OpenAIConfig:
    """Centralized OpenAI configuration"""
    
    # Single source of truth for prompts
    SITE_SURVEY_PROMPT = """
    Site survey : Extract data from the door or window drawing in the PDF file.

    The Definitive Guideline for Window & Door Data Extraction
    Core Principles: The Hierarchy of Truth
    1. The Drawing is a 3D Representation: Do not assume a flat layout. Actively look for clues indicating corners, bays, or other three-dimensional shapes.
    2. Every Piece of Data is Intentional: Do not assume typos. If dimensions seem to conflict (e.g., 2800 vs. 2840), there is likely a structural reason, such as a stepped floor.
    3. Reconcile, Don't Assume: The final answer must be a perfect reconciliation of the overall dimensions, the visual layout, the panel count, the divider count, and all textual annotations.
    4. You must complete the panel count before finding panel details, and do not let visual information from the drawing override the specific text-based rules for panel count.

    Phase 1: Calculating “Total panels”
    Step 1: Count the “Total panels”
    ** Read the panel-count guideline extremely carefully and follow it strictly **
    - Ignored the visual drawing for the panel count and strictly followed the text rule.
    - “Total panels” can be determined from the Product type column in the data table.
    - The number in parentheses ( ) indicates the quantity of that specific type of door/window. For example: 4-panels sliding door (2) means two four-panel sliding doors. If there is no number in parentheses, it means there is only one.
    - “2 Sliding door,” “3-panel sliding,” “Tilt and Slide window,” or “SG Casement window” — even if there is a number in front, they are counted as 1 panel if there is no number in parentheses after them. However, if it is written as 3-panel sliding door (2), then it is counted as 2 panels.

    Examples of counting “Total panels”:
    - 2 Sliding door + Fixed windoe (2) → Total panels = 3
    - Tilt and Slide window + Fixed → Total panels = 2
    - 3-panel 3-rail sliding large → Total panels = 1
    - 4panel 2track sliding door(2) → Total panels = 2
    - SG Terrace R(2)+Awning slit+Fix → Total panels = 4


    Phase 2: Macro-Analysis (Understand the Shape and Scope)
    Step 2: Establish Overall Dimensions
    - Identify the official Opening width (Wo) and Opening Height (Ho) from the data table. These represent the absolute maximum boundaries of the assembly.

    Phase 3: Data Assignment (Attribute Details to Each Component)
    Step 3: Assign Panel Types and Dimensions
    - For each panel counted in Step 1, assign its type and calculate its precise dimensions.
    - **CRITICAL PANEL ORDER**: Follow the LEFT-TO-RIGHT sequence from the Product Type:
    - "Airflow door + Casement window + Fix window" means:
        Panel1 = Airflow door (first mentioned = leftmost)
        Panel2 = Casement window (second mentioned = middle/right)
        Panel3 = Fixed window (if present)
    - Always maintain the order as written in the Product Type column
    - First mentioned type = Panel1, Second mentioned = Panel2, etc. 
    
    Output example
    Ref: D1
    Opening width: 1600
    Opening Height: 2450
    Total panels: 3
    Panel1-type: Airflow door
    Panel1-width: 1000
    Panel1-height: 2150
    Panel2-type: Casement window
    Panel2-width: 600
    Panel2-height: 1420
    Panel3-type: Fixed window
    Panel3-width: 1000
    Panel3-height: 300

    - Panel Dimensions:
    1.Use Explicit Labels First: Always prioritize a dimension written directly on or pointing to a specific panel.
    2.Deduce from Section Totals: Calculate a missing dimension by subtracting known dimensions from a larger section's total.
    3.Reconcile with Overall Dimensions: Ensure all panel dimensions within a given wall sum up correctly to that wall's total width and height.
    4.Resolve Conflicts with Structural Logic: If an explicit label (H=2800) conflicts with the overall height (Ho=2840), find the structural reason.

    Phase 4: Final Verification
    Step 4: The Sanity Check
    - Does your final Total panels count match the visual deconstruction?
    - Do all individual panel dimensions logically add up and respect the assembly's geometry (including any steps or angles)?

    Tip: How to identify a fixed panel
    A fixed panel usually has the letter “F” at the bottom-left corner, but not always. Don’t rely solely on whether the letter F is present—look at the overall drawing to make the judgment. However, if the panel does have the letter F, then it is definitely a fixed panel.

    Output example
    Ref: W1.1
    Opening width: 1200
    Opening Height: 2300
    Total panels: 3
    Panel1-type: Single casement window
    Panel1-width: 500
    Panel1-height: 1400
    Panel2-type: Fixed window
    Panel2-width: 500
    Panel2-height: 900
    Panel3-type: Fixed window
    Panel3-width: 700
    Panel3-height: 2300

    Ref: W2
    Opening width: 5005
    Opening Height: 2430
    Total panels: 1
    Panel1-type: Single casement window

    Note:
    - Respond with only the output.
    - No additional explanations are required."""

    ELE_PROMPT = """
    ELE : Extract data from the door or window drawing in the PDF file.

    **CRITICAL REFERENCE IDENTIFICATION RULES:**
    - The "Ref" must ONLY be the short Reference Code from the data table (like W12, D1, W8.1)
    - NEVER use the Product name number (like 204882711000, 204882711200) as the Ref
    - Reference Code is typically 2-6 characters: Letter + Number format (W12, D1, W8.1, etc.)
    - Product name numbers are long (10+ digits) and should be COMPLETELY IGNORED for Ref field
    - Look specifically in the data table rows, not in headers or product information

    The Definitive Guideline for Window & Door Data Extraction
    Core Principles: The Hierarchy of Truth
    1. The Drawing is a 3D Representation: Do not assume a flat layout. Actively look for clues indicating corners, bays, or other three-dimensional shapes.
    2. Every Piece of Data is Intentional: Do not assume typos. If dimensions seem to conflict (e.g., 2800 vs. 2840), there is likely a structural reason, such as a stepped floor.
    3. Reconcile, Don't Assume: The final answer must be a perfect reconciliation of the overall dimensions, the visual layout, the panel count, the divider count, and all textual annotations.
    4. You must complete the panel count before finding panel details, and do not let visual information from the drawing override the specific text-based rules for panel count.

    Phase 1: Calculating "Total panels"
    Step 1: Count the "Total panels"
    ** Read the panel-count guideline extremely carefully and follow it strictly **
    - Ignored the visual drawing for the panel count and strictly followed the text rule.
    - "Total panels" can be determined from the Description field in the data table.
    - The number in parentheses ( ) indicates the quantity of that specific type of door/window. For example: 4-panels sliding door (2) means two four-panel sliding doors. If there is no number in parentheses, it means there is only one.
    - "2 Sliding door," "3-panel sliding," "Tilt and Slide window," or "SG Casement window" — even if there is a number in front, they are counted as 1 panel if there is no number in parentheses after them. However, if it is written as 3-panel sliding door (2), then it is counted as 2 panels.
    - Any text at the very end of the description (e.g., WE70, ATIS, WE-Plus, ATIS) is the product model or series name. These must be completely ignored for the panel count.

    Examples of counting "Total panels":
    - 2 Sliding C + Fixed (2) → Total panels = 3
    - Tilt and Slide window + Fixed → Total panels = 2
    - 3-panel 3-rail sliding large → Total panels = 1
    - 4panel 2track sliding door(2) → Total panels = 2
    - SG Terrace R(2)+Awning slit+Fix → Total panels = 4

    Phase 2: Macro-Analysis (Understand the Shape and Scope)
    Step 2: Establish Overall Dimensions
    - Identify the official Opening width (Wo) and Opening Height (Ho) from the data table. These represent the absolute maximum boundaries of the assembly.

    Phase 3: Data Assignment (Attribute Details to Each Component)
    Step 3: Assign Panel Types and Dimensions Based on Description Order
    ** Panel Ordering Rule: The order of panels must follow the sequence in the Description field **
    - Parse the Description field from left to right, separating panels by "+" symbols
    - Assign Panel1, Panel2, Panel3, etc. based on the order they appear in the Description
    - Example: "Airflow Door L+SG Casement R+Fix WE" → Panel1 = Airflow Door, Panel2 = SG Casement, Panel3 = Fixed window
    - For each panel counted in Step 1, assign its type and calculate its precise dimensions.
    - Extract the exact panel type from the description: 
        - "Airflow Door L+" = "Airflow Door" (not "Single casement door") 
        - "SG Casement R+" = "SG Casement window"
        - "2Sliding B+" = "2Sliding B" (not "Sliding window")
        - "Fix" = "Fixed window"
    - Panel Dimensions:
    1.Use Explicit Labels First: Always prioritize a dimension written directly on or pointing to a specific panel.
    2.Deduce from Section Totals: Calculate a missing dimension by subtracting known dimensions from a larger section's total.
    3.Reconcile with Overall Dimensions: Ensure all panel dimensions within a given wall sum up correctly to that wall's total width and height.
    4.Resolve Conflicts with Structural Logic: If an explicit label (H=2800) conflicts with the overall height (Ho=2840), find the structural reason.

    Phase 4: Final Verification
    Step 4: The Sanity Check
    - Does your final Total panels count match the visual deconstruction?
    - Do all individual panel dimensions logically add up and respect the assembly's geometry (including any steps or angles)?
    - Does the panel order match the sequence in the Description field?

    Tip: How to identify a fixed panel
    A fixed panel usually has the letter "F" at the bottom-left corner, but not always. Don't rely solely on whether the letter F is present—look at the overall drawing to make the judgment. However, if the panel does have the letter F, then it is definitely a fixed panel.

    Output example
    Ref: W1.1
    Opening width: 1200
    Opening Height: 2300
    Total panels: 3
    Panel1-type: Airflow Door
    Panel1-width: 500
    Panel1-height: 1400
    Panel2-type: SG Casement window
    Panel2-width: 500
    Panel2-height: 900
    Panel3-type: Fixed window
    Panel3-width: 700
    Panel3-height: 2300

    Ref: W2
    Opening width: 5005
    Opening Height: 2430
    Total panels: 1
    Panel1-type: Single casement window

    Note:
    - Respond with only the output.
    - No additional explanations are required.
    - Some pages may have multiple ref.
    - Some pages have duplicate references. If the duplicate references have the same width and height, remove the duplicate references. If the width and height are different, do not remove them.
    """

    # Centralized model and settings
    MODEL = "google/gemini-2.5-pro"
    TEMPERATURE = 0
    
    # Headers
    HEADERS = {
        "HTTP-Referer": "https://format-tostem-a9n5.onrender.com",
        "X-Title": "TOSTEM Document Analysis Tool",
    }

class OpenAIService:
    """Enhanced OpenAI service with separate parsers"""
    
    def __init__(self, client):
        self.client = client
        self.total_tokens_used = 0
        
    def convert_pdf_to_image(self, pdf_path, page_num=0):
        """Convert PDF page to base64 image"""
        try:
            doc = fitz.open(pdf_path)
            page = doc[page_num]
            
            # Render page as image
            mat = fitz.Matrix(1.5, 1.5)  # 2x zoom for better quality
            pix = page.get_pixmap(matrix=mat)
            img_data = pix.tobytes("png")
            
            # Convert to base64
            img_b64 = base64.b64encode(img_data).decode('utf-8')
            doc.close()
            
            return f"data:image/png;base64,{img_b64}"
            
        except Exception as e:
            logger.error(f"Error converting PDF to image: {e}")
            return None
        
    def call_openai_api_with_dynamic_tokens(self, image_url, max_tokens, document_type="site_survey"):
        """OPTIMIZED: OpenAI API call with dynamic token allocation"""
        try:
            # Select appropriate prompt
            if document_type == "ele":
                prompt = OpenAIConfig.ELE_PROMPT
            else:
                prompt = OpenAIConfig.SITE_SURVEY_PROMPT
            
            logger.info(f"Calling OpenAI API with {max_tokens} tokens for {document_type}")
            
            response = self.client.chat.completions.create(
                extra_headers=OpenAIConfig.HEADERS,
                model=OpenAIConfig.MODEL,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": prompt
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": image_url
                                }
                            }
                        ]
                    }
                ],
                max_tokens=max_tokens,  # ใช้ dynamic tokens
                temperature=OpenAIConfig.TEMPERATURE,
                timeout=1800
            )
            
            if hasattr(response, 'usage') and response.usage:
                tokens_used = response.usage.total_tokens
                self.total_tokens_used += tokens_used
                logger.info(f"API call used {tokens_used} tokens (limit: {max_tokens}, total: {self.total_tokens_used})")
            
            if hasattr(response, 'choices') and len(response.choices) > 0:
                choice = response.choices[0]
                if hasattr(choice, 'message') and hasattr(choice.message, 'content'):
                    return choice.message.content
            
            logger.warning("OpenAI API returned empty or invalid response")
            return None
            
        except Exception as e:
            logger.error(f"OpenAI API error: {e}")
            return None
    
    def extract_site_survey_data(self, pdf_path):
        """Extract site survey data with FIXED tokens (800 per page)"""
        self.total_tokens_used = 0  # Reset counter
        
        try:
            extracted_data = {}
            doc = fitz.open(pdf_path)
            total_pages = doc.page_count
            
            # Site Survey: Fixed tokens per page
            tokens_per_page = RevisedTokenConfig.get_tokens_for_site_survey()
            logger.info(f"Starting site survey extraction: {total_pages} pages, {tokens_per_page} tokens each")
            
            for page_num in range(total_pages):
                try:
                    logger.info(f"Processing site survey page {page_num + 1}/{total_pages}")
                    
                    # Convert page to image
                    image_url = self.convert_pdf_to_image(pdf_path, page_num)
                    if not image_url:
                        logger.warning(f"Failed to convert site page {page_num + 1} to image")
                        continue
                    
                    # Call OpenAI API with fixed tokens
                    content = self.call_openai_api_with_dynamic_tokens(
                        image_url, tokens_per_page, "site_survey"
                    )
                   
                    if content:
                        # Parse response with site survey parser
                        parsed_data = self._parse_site_survey_response(content)
                        if parsed_data:
                            extracted_data.update(parsed_data)
                            logger.info(f"Site survey page {page_num + 1}: Found {len(parsed_data)} items")
                        else:
                            logger.info(f"Site survey page {page_num + 1}: No data found")
                    else:
                        logger.warning(f"Site survey page {page_num + 1}: OpenAI returned empty content")
                
                except Exception as page_error:
                    logger.error(f"Error processing site survey page {page_num + 1}: {page_error}")
                    continue
            
            doc.close()
            logger.info(f"Site survey extraction completed: {len(extracted_data)} total items")
            logger.info(f"Total tokens used for site survey: {self.total_tokens_used}")
            return extracted_data
            
        except Exception as e:
            logger.error(f"Error extracting site survey data: {e}")
            return {}
                
    def extract_ele_data(self, pdf_path):
        """Extract ELE data with DYNAMIC tokens based on refs per page"""
        # Don't reset token counter here - accumulate from site survey
        ele_start_tokens = self.total_tokens_used
        
        try:
            extracted_data = {}
            doc = fitz.open(pdf_path)
            total_pages = doc.page_count
            logger.info(f"Starting ELE extraction: {total_pages} pages with dynamic tokens")
            
            for page_num in range(total_pages):
                try:
                    logger.info(f"Processing ELE page {page_num + 1}/{total_pages}")
                    
                    # STEP 1: Analyze page to estimate ref count
                    estimated_refs = ELEPageAnalyzer.estimate_ref_count(pdf_path, page_num)
                    tokens_for_page = RevisedTokenConfig.get_tokens_for_ele(estimated_refs)
                    
                    logger.info(f"ELE Page {page_num + 1}: {estimated_refs} refs estimated, using {tokens_for_page} tokens")
                    
                    # STEP 2: Convert page to image
                    image_url = self.convert_pdf_to_image(pdf_path, page_num)
                    if not image_url:
                        logger.warning(f"Failed to convert ELE page {page_num + 1} to image")
                        continue
                    
                    # STEP 3: Call OpenAI API with dynamic tokens
                    content = self.call_openai_api_with_dynamic_tokens(
                        image_url, tokens_for_page, "ele"
                    )
                        
                    if content:
                        # Parse response with ELE parser
                        parsed_data = self._parse_ele_response(content)
                        if parsed_data:
                            extracted_data.update(parsed_data)
                            logger.info(f"ELE page {page_num + 1}: Found {len(parsed_data)} items")
                        else:
                            logger.info(f"ELE page {page_num + 1}: No data found")
                    else:
                        logger.warning(f"ELE page {page_num + 1}: OpenAI returned empty content")
                   
                except Exception as page_error:
                    logger.error(f"Error processing ELE page {page_num + 1}: {page_error}")
                    continue
            
            doc.close()
            ele_tokens_used = self.total_tokens_used - ele_start_tokens
            logger.info(f"ELE extraction completed: {len(extracted_data)} total items")
            logger.info(f"Tokens used for ELE: {ele_tokens_used}")
            logger.info(f"Total tokens used: {self.total_tokens_used}")
            return extracted_data
        
        except Exception as e:
            logger.error(f"Error extracting ELE data: {e}")
            return {}

    def get_total_tokens_used(self):
        """Get total tokens used in this session"""
        return self.total_tokens_used
    
    def _parse_site_survey_response(self, content):
        """Parse site survey response - focused on survey data"""
        site_data = {}
        
        if not content:
            logger.warning("Site survey OpenAI response is empty")
            return site_data
        
        content_str = str(content)
        logger.info(f"=== FULL SITE SURVEY CONTENT ===")
        logger.info(content_str)
        logger.info("=== END FULL CONTENT ===")

        if "Ref:" in content_str:
            sections = content_str.split('Ref:')
            logger.info(f"Site survey: Found {len(sections)-1} sections")
            
            for i, section in enumerate(sections[1:], 1):
                try:
                    lines = section.strip().split('\n')
                    if not lines:
                        continue
                    
                    ref = lines[0].strip()
                    logger.info(f"Site survey processing ref: '{ref}'")
                    
                    item_data = {
                        'ref': ref,
                        'opening_width': None,
                        'opening_height': None,
                        'product_type': '',
                        'panels': []
                    }
                    
                    current_panel = {}
                    
                    for line_num, line in enumerate(lines[1:], 1):
                        line = line.strip()
                        
                        if line.startswith('Opening width:'):
                            width_match = re.search(r'Opening width:\s*(\d+)', line)
                            if width_match:
                                item_data['opening_width'] = int(width_match.group(1))
                                logger.info(f"    Site survey width: {item_data['opening_width']}")
                        
                        elif line.startswith('Opening Height:') or line.startswith('Opening height:'):
                            height_match = re.search(r'Opening [Hh]eight:\s*(\d+)', line)
                            if height_match:
                                item_data['opening_height'] = int(height_match.group(1))
                                logger.info(f"    Site survey height: {item_data['opening_height']}")
                        
                        elif line.startswith('Product type:') or line.startswith('Total panels:'):
                            if line.startswith('Product type:'):
                                item_data['product_type'] = line.replace('Product type:', '').strip()
                                logger.info(f"    Site survey product type: {item_data['product_type']}")
                            # If we have panel information but no explicit product type, derive it
                            elif line.startswith('Total panels:') and not item_data['product_type']:
                                # Will derive product type from panels after parsing
                                pass
                        
                        # Panel parsing with fixed regex
                        elif re.match(r'Panel\d+-type:', line):
                            if current_panel.get('type'):
                                item_data['panels'].append(current_panel.copy())
                            
                            panel_type = line.split(':', 1)[1].strip() if ':' in line else ''
                            current_panel = {
                                'type': panel_type,
                                'width': None,
                                'height': None
                            }
                        
                        elif re.match(r'Panel\d+-width:', line):
                            width_match = re.search(r'Panel\d+-width:\s*(\d+)', line)
                            if width_match:
                                current_panel['width'] = int(width_match.group(1))
                                logger.info(f"    Site survey panel width: {current_panel['width']}")
                        
                        elif re.match(r'Panel\d+-height:', line):
                            height_match = re.search(r'Panel\d+-height:\s*(\d+)', line)
                            if height_match:
                                current_panel['height'] = int(height_match.group(1))
                                logger.info(f"    Site survey panel height: {current_panel['height']}")
                    
                    # Save last panel
                    if current_panel.get('type'):

                        if 'width' not in current_panel or current_panel['width'] is None:
                            current_panel['width'] = 0  # หรือใส่ค่า default
                        if 'height' not in current_panel or current_panel['height'] is None:
                            current_panel['height'] = 0  # หรือใส่ค่า default
                        
                        item_data['panels'].append(current_panel.copy())
                        logger.info(f"Added final panel: {current_panel}")
                    
                    # **FIX: Derive product type from panels if not explicitly provided**
                    if not item_data['product_type'] and item_data['panels']:
                        item_data['product_type'] = self._derive_product_type_from_panels(item_data['panels'])
                        logger.info(f"    Derived product type: {item_data['product_type']}")
                    
                    # Add to results if valid
                    if item_data['opening_width'] and item_data['opening_height']:
                        site_data[ref] = item_data
                        logger.info(f"✅ Site survey parsed {ref}: {item_data['opening_width']}x{item_data['opening_height']}")
                
                except Exception as e:
                    logger.error(f"Error parsing site survey section {i}: {e}")
                    continue
        
        logger.info(f"Site survey final parsed data: {len(site_data)} items - {list(site_data.keys())}")
        return site_data
    
    def _derive_product_type_from_panels(self, panels):
        """Derive a meaningful product type description from panel information"""
        if not panels:
            return ''
        
        panel_types = []
        for panel in panels:
            panel_type = panel.get('type', '').strip()
            if panel_type and panel_type not in panel_types:
                panel_types.append(panel_type)
        
        if len(panel_types) == 1:
            # Single type across all panels
            return panel_types[0]
        elif len(panel_types) > 1:
            # Multiple types - create combination description
            return ' + '.join(panel_types)
        else:
            return ''

    def _parse_ele_response(self, content):
        """Parse ELE response - focused on technical drawings"""
        ele_data = {}
        
        if not content:
            logger.warning("ELE OpenAI response is empty")
            return ele_data
        
        content_str = str(content)
        logger.info(f"=== FULL SITE SURVEY CONTENT ===")
        logger.info(content_str)
        logger.info("=== END FULL CONTENT ===")

        if "Ref:" in content_str:
            sections = content_str.split('Ref:')
            logger.info(f"ELE: Found {len(sections)-1} sections")
            
            for i, section in enumerate(sections[1:], 1):
                try:
                    lines = section.strip().split('\n')
                    if not lines:
                        continue
                    
                    ref = lines[0].strip()
                    logger.info(f"ELE processing ref: '{ref}'")
                    
                    item_data = {
                        'ref': ref,
                        'opening_width': None,
                        'opening_height': None,
                        'element_type': '',
                        'series': '',
                        'panels': []
                    }
                    
                    current_panel = {}
                    
                    for line_num, line in enumerate(lines[1:], 1):
                        line = line.strip()
                        
                        if line.startswith('Opening width:'):
                            width_match = re.search(r'Opening width:\s*(\d+)', line)
                            if width_match:
                                item_data['opening_width'] = int(width_match.group(1))
                                logger.info(f"    ELE width: {item_data['opening_width']}")
                        
                        elif line.startswith('Opening Height:') or line.startswith('Opening height:'):
                            height_match = re.search(r'Opening [Hh]eight:\s*(\d+)', line)
                            if height_match:
                                item_data['opening_height'] = int(height_match.group(1))
                                logger.info(f"    ELE height: {item_data['opening_height']}")
                        
                        elif line.startswith('Element type:') or line.startswith('Product type:') or line.startswith('Total panels:'):
                            if line.startswith('Element type:'):
                                item_data['element_type'] = line.replace('Element type:', '').strip()
                                logger.info(f"    ELE element type: {item_data['element_type']}")
                            elif line.startswith('Product type:'):
                                item_data['element_type'] = line.replace('Product type:', '').strip()
                                logger.info(f"    ELE product type: {item_data['element_type']}")
                            # If we have panel information but no explicit element type, derive it
                            elif line.startswith('Total panels:') and not item_data['element_type']:
                                # Will derive element type from panels after parsing
                                pass
                        
                        elif line.startswith('Series:'):
                            item_data['series'] = line.replace('Series:', '').strip()
                            logger.info(f"    ELE series: {item_data['series']}")
                        
                        # Panel parsing with fixed regex
                        elif re.match(r'Panel\d+-type:', line):
                            if current_panel.get('type'):
                                item_data['panels'].append(current_panel.copy())
                                logger.info(f"Added panel: {current_panel}")
                            
                            panel_type = line.split(':', 1)[1].strip() if ':' in line else ''
                            current_panel = {
                                'type': panel_type,
                                'width': None,
                                'height': None
                            }
                            logger.info(f"Started new panel: type='{panel_type}'")

                        elif re.match(r'Panel\d+-width:', line):
                            width_match = re.search(r'Panel\d+-width:\s*(\d+)', line)
                            if width_match:
                                current_panel['width'] = int(width_match.group(1))
                                logger.info(f"Set panel width: {current_panel['width']}")

                        elif re.match(r'Panel\d+-height:', line):
                            height_match = re.search(r'Panel\d+-height:\s*(\d+)', line)
                            if height_match:
                                current_panel['height'] = int(height_match.group(1))
                                logger.info(f"Set panel height: {current_panel['height']}")

                    # Save last panel
                    if current_panel.get('type'):
                        item_data['panels'].append(current_panel.copy())
                        logger.info(f"Added FINAL panel: {current_panel}")

                    logger.info(f"=== PARSING SUMMARY FOR {ref} ===")
                    logger.info(f"Total panels: {len(item_data['panels'])}")
                    for i, panel in enumerate(item_data['panels']):
                        logger.info(f"Panel {i+1}: {panel}")

                    # **FIX: Derive element type from panels if not explicitly provided**
                    if not item_data['element_type'] and item_data['panels']:
                        item_data['element_type'] = self._derive_element_type_from_panels(item_data['panels'])
                        logger.info(f"    Derived element type: {item_data['element_type']}")
                    
                    # Add to results if valid
                    if item_data['opening_width'] and item_data['opening_height']:
                        ele_data[ref] = item_data
                        logger.info(f"✅ ELE parsed {ref}: {item_data['opening_width']}x{item_data['opening_height']}")
                
                except Exception as e:
                    logger.error(f"Error parsing ELE section {i}: {e}")
                    continue
        
        logger.info(f"ELE final parsed data: {len(ele_data)} items - {list(ele_data.keys())}")
        return ele_data

    def _derive_element_type_from_panels(self, panels):
        """Derive a meaningful element type description from panel information"""
        if not panels:
            return ''
        
        panel_types = []
        for panel in panels:
            panel_type = panel.get('type', '').strip()
            if panel_type and panel_type not in panel_types:
                if panel_type.lower() not in ['customer approve', 'customer approved', '-', '']:
                    panel_types.append(panel_type)
        
        if len(panel_types) == 1:
            # Single type across all panels
            return panel_types[0]
        elif len(panel_types) > 1:
            # Multiple types - create combination description
            return ' + '.join(panel_types)
        else:
            return ''

class PDFDataExtractor:
    """Enhanced PDF data extractor with smart AI usage - only processes complex items with AI"""
    
    @staticmethod
    def extract_site_survey_data(file_path):
        """Extract site survey data - ใช้ AI เฉพาะรายการที่มีศักยภาพมีบานย่อย"""
        results = []
        
        logger.info("=== STARTING SITE SURVEY EXTRACTION WITH SMART AI USAGE ===")
        
        # STEP 1: Pre-scan เพื่อหาว่าข้อมูลไหนน่าจะมีบานย่อย
        items_need_ai = []
        all_items = []
        
        try:
            with pdfplumber.open(file_path) as pdf:
                logger.info(f"Pre-scanning site survey PDF with {len(pdf.pages)} pages")
                
                for page_num, page in enumerate(pdf.pages, 1):
                    tables = page.extract_tables()
                    if not tables:
                        continue
                    
                    for table in tables:
                        if not table:
                            continue
                        
                        for row in table:
                            if not row or len(row) < 8:
                                continue
                            
                            if row[0] and re.match(r'^[WDA]\w*\d*(\.\d+)?$', str(row[0]).strip()):
                                ref = str(row[0]).strip()
                                product_type = str(row[2]).strip() if len(row) > 2 and row[2] else ""
                                
                                # ตรวจสอบว่าน่าจะมีบานย่อยหรือไม่
                                needs_ai = PDFDataExtractor._likely_has_subpanels(product_type, ref)
                                
                                basic_item = {
                                    'ref': ref,
                                    'row': row,
                                    'page_num': page_num,
                                    'needs_ai': needs_ai,
                                    'product_type': product_type
                                }
                                
                                all_items.append(basic_item)
                                
                                if needs_ai:
                                    items_need_ai.append(basic_item)
                                    logger.info(f"📋 {ref}: มีศักยภาพมีบานย่อย -> ใช้ AI")
                                else:
                                    logger.info(f"⚡ {ref}: ไม่มีบานย่อย -> ข้าม AI")
        
        except Exception as e:
            logger.error(f"Error in pre-scanning: {e}")
            return []
        
        # STEP 2: ใช้ AI เฉพาะกับรายการที่จำเป็น
        openai_site_data = {}
        
        if items_need_ai and openai_client:
            logger.info(f"Using AI for {len(items_need_ai)} items that likely have sub-panels")
            
            try:
                openai_service = OpenAIService(openai_client)
                
                # สร้าง list หน้าที่ต้องการ AI (ไม่ซ้ำ)
                pages_need_ai = list(set([item['page_num'] for item in items_need_ai]))
                
                for page_num in pages_need_ai:
                    try:
                        logger.info(f"Processing page {page_num} with AI")
                        
                        tokens_per_page = RevisedTokenConfig.get_tokens_for_site_survey()
                        image_url = openai_service.convert_pdf_to_image(file_path, page_num - 1)
                        
                        if image_url:
                            content = openai_service.call_openai_api_with_dynamic_tokens(
                                image_url, tokens_per_page, "site_survey"
                            )
                            
                            if content:
                                parsed_data = openai_service._parse_site_survey_response(content)
                                openai_site_data.update(parsed_data)
                                logger.info(f"AI processed page {page_num}: {len(parsed_data)} items extracted")
                                
                    except Exception as e:
                        logger.warning(f"AI processing failed for page {page_num}: {e}")
                        continue
                
                # Store token usage
                PDFDataExtractor.site_tokens_used = openai_service.get_total_tokens_used()
                logger.info(f"AI processing completed. Tokens used: {PDFDataExtractor.site_tokens_used}")
            
            except Exception as e:
                logger.error(f"Error in AI processing: {e}")
                PDFDataExtractor.site_tokens_used = 0
        
        elif items_need_ai and not openai_client:
            logger.warning(f"Found {len(items_need_ai)} items that need AI but OpenAI client not available - processing with basic method")
            PDFDataExtractor.site_tokens_used = 0
        
        else:
            logger.info("No items need AI processing - saving 100% tokens!")
            PDFDataExtractor.site_tokens_used = 0
        
        # STEP 3: รวมผลลัพธ์
        final_results = []
        
        for item in all_items:
            try:
                ref = item['ref']
                row = item['row']
                page_num = item['page_num']
                needs_ai = item['needs_ai']
                
                # ประมวลผลข้อมูลแถว
                if needs_ai and ref in openai_site_data:
                    # ใช้ข้อมูล AI
                    data_item = PDFDataExtractor._process_site_survey_row_with_ai(
                        row, page_num, openai_site_data
                    )
                    processing_type = "AI-enhanced"
                else:
                    # ใช้ข้อมูลจากตารางเท่านั้น
                    data_item = PDFDataExtractor._process_site_survey_row_basic(
                        row, page_num
                    )
                    processing_type = "basic processing"
                
                if data_item:
                    data_item['discovery_order'] = len(final_results)
                    final_results.append(data_item)
                    logger.info(f"✅ Added {ref}: {processing_type}")
                    
            except Exception as e:
                logger.error(f"Error processing item {item.get('ref', 'unknown')}: {e}")
                continue
        
        logger.info(f"Site survey final result: {len(final_results)} items")
        ai_count = len([item for item in final_results if item.get('Processing_Type') == 'AI_Enhanced'])
        basic_count = len([item for item in final_results if item.get('Processing_Type') == 'Basic'])
        logger.info(f"Processing summary: {ai_count} AI-enhanced, {basic_count} basic processing")
        
        return final_results

    @staticmethod
    def _likely_has_subpanels(product_type, ref):
        """ตรวจสอบว่าข้อมูลนี้น่าจะมีบานย่อยหรือไม่ - ปรับปรุงการตรวจสอบ Product Type"""
        if not product_type:
            return False
        
        product_clean = str(product_type).strip()
        
        # **CRITICAL FIX: ทำความสะอาดก่อนตรวจสอบ**
        product_cleaned = PDFDataExtractor._fix_garbled_text(product_clean)
        
        # **NEW LOGIC: ตรวจสอบ Product Type โดยเฉพาะ**
        logger.info(f"Checking Product Type for {ref}: '{product_clean}' -> cleaned: '{product_cleaned}'")
        
        # Pattern 1: มี + ในข้อความ (บ่งบอกการรวมหลายชิ้น)
        if '+' in product_cleaned:
            logger.info(f"Found '+' in Product Type: {product_cleaned} -> USE AI")
            return True
        
        # Pattern 2: มี (2), (3), (4) ในข้อความ (บ่งบอกจำนวน)
        import re
        number_pattern = re.search(r'\((\d+)\)', product_cleaned)
        if number_pattern:
            number = int(number_pattern.group(1))
            if number >= 2:
                logger.info(f"Found ({number}) in Product Type: {product_cleaned} -> USE AI")
                return True
        
        # Default: ไม่ซับซ้อน -> ข้าม AI
        logger.info(f"Product Type is simple: {product_cleaned} -> SKIP AI")
        return False
    
    @staticmethod
    def _clean_product_type(product_type):
        """ทำความสะอาด product type จากข้อมูล site survey"""
        if not product_type:
            return ""
        
        # แปลงเป็น string และตัดช่องว่าง
        cleaned = str(product_type).strip()
        
        # ลบ prefix ที่ไม่ควรมี
        prefixes_to_remove = [
            "Customer approve",
            "customer approve", 
            "Customer Approve",
            "CUSTOMER APPROVE"
        ]
        
        for prefix in prefixes_to_remove:
            if cleaned.lower().startswith(prefix.lower()):
                cleaned = cleaned[len(prefix):].strip()
                break
        
        # ลบช่องว่างเกิน
        cleaned = " ".join(cleaned.split())
        
        # ตรวจสอบว่ามีเนื้อหาจริง
        if len(cleaned) < 3:
            return product_type  # คืนค่าต้นฉบับถ้าการทำความสะอาดทำให้สั้นเกินไป
        
        return cleaned
    
    @staticmethod     
    def _fix_garbled_text(text):
        """แก้ไข garbled text เช่น 'AAwwnnnniinngg' -> 'Awning'"""
        if not text:
            return ""
        
        text = str(text).strip()
        
        # แก้ไขการเว้นวรรคใน parentheses ก่อน เช่น ( 2) -> (2)
        text = re.sub(r'\(\s+(\d+)\s*\)', r'(\1)', text)
        
        # ลบ "Customer approve" ก่อน
        prefixes_to_remove = [
            "Customer approve",
            "customer approve", 
            "Customer Approve", 
            "CUSTOMER APPROVE"
        ]
        
        for prefix in prefixes_to_remove:
            if text.lower().startswith(prefix.lower()):
                text = text[len(prefix):].strip()
                break
        
        # แก้ไข garbled text pattern
        # Pattern: AAwwnnnniinngg -> Awning
        if re.search(r'([A-Za-z])\1{2,}', text):
            # เอาตัวอักษรที่ซ้ำออก
            cleaned = re.sub(r'([A-Za-z])\1+', r'\1', text)
            
            # แปลงเป็นรูปแบบปกติ
            cleaned = cleaned.lower()
            
            # Map ชื่อที่พบบ่อย
            common_mappings = {
                'awning': 'Awning window',
                'awnning': 'Awning window', 
                'casement': 'Single casement window',
                'sliding': 'Sliding window',
                'fixed': 'Fix window',
                'fix': 'Fix window'
            }
            
            # ตรวจสอบว่าตรงกับ mapping ไหน
            for key, value in common_mappings.items():
                if key in cleaned:
                    # ตรวจสอบ pattern เพิ่มเติม
                    if '(2)' in text:
                        if 'awning' in key or 'awnning' in key:
                            return f"Awning window (2)"
                    return value
            
            logger.info(f"Fixed garbled text: '{text}' -> '{cleaned}'")
            return cleaned.title()  # Capitalize first letter
        
        # ถ้าไม่ใช่ garbled text ให้ทำความสะอาดปกติ
        cleaned = " ".join(text.split())
        return cleaned if cleaned and len(cleaned) > 2 else text

    @staticmethod 
    def _process_site_survey_row_basic(row, page_num):
        """ประมวลผลแถว site survey แบบพื้นฐาน - เวอร์ชันที่แก้ไขแล้ว"""
        ref = str(row[0]).strip()
        series = str(row[1]).strip() if len(row) > 1 and row[1] else ""
        
        # **FIX: การดึง product type ที่ดีขึ้นพร้อมการทำความสะอาด**
        raw_product_type = str(row[2]).strip() if len(row) > 2 and row[2] else ""
        
        # ทำความสะอาด product type อย่างถูกต้อง - แก้ไข garbled text
        product_type = PDFDataExtractor._fix_garbled_text(raw_product_type)
        
        # ดึงขนาดจากตาราง
        survey_wo = PDFDataExtractor._extract_numeric_value(row[4] if len(row) > 4 else None)
        survey_ho = PDFDataExtractor._extract_numeric_value(row[5] if len(row) > 5 else None)
        
        if not survey_wo or not survey_ho:
            logger.warning(f"No valid dimensions for {ref}")
            return None
        
        # ดึงข้อมูล insect screen
        insect_screen = "No"
        for i in range(6, len(row)):
            if row[i]:
                cell_value = str(row[i]).strip().lower()
                if cell_value in ['yes', 'y', '1', 'มี', 'ใช่']:
                    insect_screen = "Yes"
                    break
        
        return {
            "Ref": ref,
            "Series": series,
            "Product Type": product_type,  # ตอนนี้ทำความสะอาดถูกต้องแล้ว
            "Survey_Wo": survey_wo,
            "Survey_Ho": survey_ho,
            "Insect_Screen": insect_screen,
            "Page": page_num,
            "OpenAI_Panels": [],
            "Total_Panels": 0,
            "Has_Panel_Details": False,
            "Processing_Type": "Basic"
        }

    @staticmethod
    def _process_site_survey_row_with_ai(row, page_num, openai_site_data):
        """ประมวลผลแถวที่มีข้อมูล AI - แก้ไขการแสดง Product Type"""
        ref = str(row[0]).strip()
        series = str(row[1]).strip() if len(row) > 1 and row[1] else ""
        
        logger.info(f"Processing site survey row for {ref} with AI data")
        
        # Try exact match first
        if openai_site_data and ref in openai_site_data:
            openai_data = openai_site_data[ref]
            # FIXED: Use the actual product_type from OpenAI response
            product_type = openai_data.get('product_type', '')
            survey_wo = openai_data.get('opening_width')
            survey_ho = openai_data.get('opening_height')
            panels = openai_data.get('panels', [])
            
            logger.info(f"✅ Using AI data for {ref}: product_type='{product_type}', {len(panels)} panels")
        
        # Try dimension matching if no exact match
        elif openai_site_data:
            logger.info(f"No exact match for {ref}, trying dimension matching...")
            
            survey_wo = PDFDataExtractor._extract_numeric_value(row[4] if len(row) > 4 else None)
            survey_ho = PDFDataExtractor._extract_numeric_value(row[5] if len(row) > 5 else None)
            
            # FIXED: Use table product type as primary source
            raw_product_type = str(row[2]).strip() if len(row) > 2 and row[2] else ""
            product_type = PDFDataExtractor._fix_garbled_text(raw_product_type)
            panels = []
            
            # Try to find OpenAI data with matching dimensions
            if survey_wo and survey_ho:
                for openai_key, openai_data in openai_site_data.items():
                    if (openai_data.get('opening_width') == survey_wo and 
                        openai_data.get('opening_height') == survey_ho):
                        panels = openai_data.get('panels', [])
                        # Only use OpenAI product_type if table product_type is empty or generic
                        ai_product_type = openai_data.get('product_type', '')
                        if ai_product_type and (not product_type or product_type in ['-', '']):
                            product_type = ai_product_type
                            logger.info(f"✅ Using AI product type for {ref}: '{product_type}'")
                        break
        
        else:
            # Fallback to table data
            raw_product_type = str(row[2]).strip() if len(row) > 2 and row[2] else ""
            product_type = PDFDataExtractor._fix_garbled_text(raw_product_type)
            survey_wo = PDFDataExtractor._extract_numeric_value(row[4] if len(row) > 4 else None)
            survey_ho = PDFDataExtractor._extract_numeric_value(row[5] if len(row) > 5 else None)
            panels = []
        
        if not survey_wo or not survey_ho:
            logger.warning(f"No valid dimensions for {ref}")
            return None
        
        # Extract insect screen
        insect_screen = "No"
        for i in range(6, len(row)):
            if row[i]:
                cell_value = str(row[i]).strip().lower()
                if cell_value in ['yes', 'y', '1', 'มี', 'ใช่']:
                    insect_screen = "Yes"
                    break
        
        return {
            "Ref": ref,
            "Series": series,
            "Product Type": product_type,  # FIXED: Always use actual product type from table or AI
            "Survey_Wo": survey_wo,
            "Survey_Ho": survey_ho,
            "Insect_Screen": insect_screen,
            "Page": page_num,
            "OpenAI_Panels": panels,
            "Total_Panels": len(panels),
            "Has_Panel_Details": len(panels) > 0,
            "Processing_Type": "AI_Enhanced"
        }

    @staticmethod
    def _extract_numeric_value(value):
        """Extract numeric value from cell with validation"""
        if not value:
            return None
        
        value_str = str(value).strip()
        if value_str.isdigit():
            num_val = int(value_str)
            # Validate reasonable range for dimensions
            if 50 <= num_val <= 8000:
                return num_val
        return None


class ELEDataExtractor:
    """Enhanced ELE data extractor with smart AI usage - only processes complex pages with AI"""

    @staticmethod
    def extract_ele_data(file_path):
        """Extract ELE data - ใช้ AI เฉพาะรายการที่ซับซ้อน และข้ามหน้า INSECT SCREEN"""
        extracted = []
        
        logger.info("=== STARTING ELE EXTRACTION WITH SMART AI USAGE ===")
        
        # STEP 1: Pre-scan เพื่อหาข้อมูลที่ต้องการ AI
        pages_need_ai = []
        basic_processing_items = []
        
        try:
            with pdfplumber.open(file_path) as pdf:
                for page_num, page in enumerate(pdf.pages, 1):
                    text = page.extract_text() or ""
                    
                    # ตรวจสอบว่าเป็นหน้า INSECT SCREEN หรือไม่
                    if ELEDataExtractor._is_insect_screen_page(text):
                        logger.info(f"🚫 ELE Page {page_num}: Skipping INSECT SCREEN page")
                        continue
                    
                    # หา references
                    refs = ELEDataExtractor._extract_valid_references(text)
                    
                    if not refs:
                        logger.info(f"ELE Page {page_num}: No valid references found")
                        continue
                    
                    logger.info(f"ELE Page {page_num}: Found valid references: {refs}")
                    
                    # ตรวจสอบว่าหน้านี้ซับซ้อนหรือไม่
                    page_complexity = ELEDataExtractor._assess_page_complexity(text, refs)
                    
                    if page_complexity['needs_ai']:
                        pages_need_ai.append({
                            'page_num': page_num,
                            'refs': refs,
                            'complexity': page_complexity,
                            'text': text,
                            'tables': page.extract_tables()
                        })
                        logger.info(f"📋 ELE Page {page_num}: ซับซ้อน ({page_complexity['reason']}) -> ใช้ AI")
                    else:
                        logger.info(f"⚡ ELE Page {page_num}: ธรรมดา -> ข้าม AI")
                        
                        # ประมวลผลแบบพื้นฐาน
                        for ref in refs:
                            basic_item = ELEDataExtractor._process_ele_basic(
                                ref, text, page.extract_tables(), page_num
                            )
                            if basic_item:
                                basic_processing_items.append(basic_item)
        
        except Exception as e:
            logger.error(f"Error in ELE pre-scanning: {e}")
            return []
        
        # เพิ่มรายการ basic processing เข้าไปก่อน
        extracted.extend(basic_processing_items)
        logger.info(f"Added {len(basic_processing_items)} basic processing items")
        
        # STEP 2: ใช้ AI เฉพาะหน้าที่ซับซ้อน
        if pages_need_ai and openai_client:
            logger.info(f"Using AI for {len(pages_need_ai)} complex ELE pages")
            
            try:
                openai_service = OpenAIService(openai_client)
                
                # Continue token counting from site survey
                if hasattr(PDFDataExtractor, 'site_tokens_used'):
                    openai_service.total_tokens_used = PDFDataExtractor.site_tokens_used
                    logger.info(f"Continuing from site survey tokens: {openai_service.total_tokens_used}")
                
                for page_info in pages_need_ai:
                    page_num = page_info['page_num']
                    estimated_refs = len(page_info['refs'])
                    
                    try:
                        tokens_for_page = RevisedTokenConfig.get_tokens_for_ele(estimated_refs)
                        image_url = openai_service.convert_pdf_to_image(file_path, page_num - 1)
                        
                        if image_url:
                            content = openai_service.call_openai_api_with_dynamic_tokens(
                                image_url, tokens_for_page, "ele"
                            )
                            
                            if content:
                                parsed_data = openai_service._parse_ele_response(content)
                                
                                # เพิ่มรายการที่ได้จาก AI
                                for ref, ai_data in parsed_data.items():

                                    ai_series = ai_data.get('series', '')
                                    if not ai_series:
                                        # ใช้ฟังก์ชันเดิมที่ทำงานได้ดี
                                        ai_series = ELEDataExtractor._extract_series_from_page_text(page_info['text'])
                                        logger.info(f"Fallback series extraction for {ref}: '{ai_series}'")

                                    enhanced_item = {
                                        "Ref": ref,
                                        "Ele_Wo": ai_data.get('opening_width'),
                                        "Ele_Ho": ai_data.get('opening_height'),
                                        "Element_Type": ai_data.get('element_type', ''),
                                        "Series": ai_series,
                                        "Page": page_num,
                                        "Page_Order": 0,
                                        "Discovery_Order": len(extracted),
                                        "OpenAI_Panels": ai_data.get('panels', []),
                                        "Has_Panel_Details": len(ai_data.get('panels', [])) > 0,
                                        "Source": "AI_Enhanced",
                                        "Processing_Type": "AI_Enhanced"
                                    }
                                    extracted.append(enhanced_item)
                                    logger.info(f"✅ AI enhanced {ref}: {enhanced_item['Ele_Wo']}×{enhanced_item['Ele_Ho']}")
                            else:
                                logger.warning(f"AI returned no content for ELE page {page_num}")
                                
                                # FIX: Fallback to basic processing for all refs on this page
                                for ref in page_info['refs']:
                                    fallback_item = ELEDataExtractor._process_ele_basic(
                                        ref, page_info['text'], page_info['tables'], page_num
                                    )
                                    if fallback_item:
                                        fallback_item['Discovery_Order'] = len(extracted)
                                        extracted.append(fallback_item)
                                        logger.info(f"🔄 Fallback processed {ref}")
                        else:
                            logger.warning(f"Failed to convert page {page_num} to image")
                            # FIX: Fallback to basic processing
                            for ref in page_info['refs']:
                                fallback_item = ELEDataExtractor._process_ele_basic(
                                    ref, page_info['text'], page_info['tables'], page_num
                                )
                                if fallback_item:
                                    fallback_item['Discovery_Order'] = len(extracted)
                                    extracted.append(fallback_item)
                                    logger.info(f"🔄 Image conversion fallback processed {ref}")
                                    
                    except Exception as e:
                        logger.warning(f"AI processing failed for ELE page {page_num}: {e}")
                        
                        # FIX: Always fallback to basic processing when AI fails
                        for ref in page_info['refs']:
                            fallback_item = ELEDataExtractor._process_ele_basic(
                                ref, page_info['text'], page_info['tables'], page_num
                            )
                            if fallback_item:
                                fallback_item['Discovery_Order'] = len(extracted)
                                extracted.append(fallback_item)
                                logger.info(f"🔄 Error fallback processed {ref}")
                        continue
                
                # Store token usage
                ELEDataExtractor.total_tokens_used = openai_service.get_total_tokens_used()
                ELEDataExtractor.ele_tokens_used = ELEDataExtractor.total_tokens_used - getattr(PDFDataExtractor, 'site_tokens_used', 0)
                
            except Exception as e:
                logger.error(f"Error in ELE AI processing: {e}")
                ELEDataExtractor.total_tokens_used = getattr(PDFDataExtractor, 'site_tokens_used', 0)
                ELEDataExtractor.ele_tokens_used = 0
        
        elif pages_need_ai and not openai_client:
            logger.warning(f"Found {len(pages_need_ai)} complex ELE pages but OpenAI client not available - using basic processing")
            
            # ประมวลผลหน้าที่ซับซ้อนด้วย basic method แทน
            for page_info in pages_need_ai:
                for ref in page_info['refs']:
                    fallback_item = ELEDataExtractor._process_ele_basic(
                        ref, page_info['text'], page_info['tables'], page_info['page_num']
                    )
                    if fallback_item:
                        fallback_item['Discovery_Order'] = len(extracted)
                        extracted.append(fallback_item)
            
            ELEDataExtractor.total_tokens_used = getattr(PDFDataExtractor, 'site_tokens_used', 0)
            ELEDataExtractor.ele_tokens_used = 0
        
        else:
            logger.info("No complex ELE pages found - saving tokens!")
            ELEDataExtractor.total_tokens_used = getattr(PDFDataExtractor, 'site_tokens_used', 0)
            ELEDataExtractor.ele_tokens_used = 0
        
        # FIX: Ensure all items have required fields
        for item in extracted:
            if 'Discovery_Order' not in item:
                item['Discovery_Order'] = 0
            if 'Processing_Type' not in item:
                item['Processing_Type'] = 'Basic'
            if 'Source' not in item:
                item['Source'] = 'Basic_Processing'
        
        # Remove duplicates
        unique_extracted = []
        seen_refs = set()
        
        for item in extracted:
            ref = item['Ref']
            if ref not in seen_refs:
                unique_extracted.append(item)
                seen_refs.add(ref)
            else:
                logger.info(f"Removed duplicate ref: {ref}")
        
        # Log summary
        ai_count = len([item for item in unique_extracted if item.get('Processing_Type') == 'AI_Enhanced'])
        basic_count = len([item for item in unique_extracted if item.get('Processing_Type') == 'Basic'])
        
        logger.info(f"ELE extraction completed: {len(unique_extracted)} items")
        logger.info(f"Processing summary: {ai_count} AI-enhanced, {basic_count} basic processing")
        logger.info(f"Token usage - Total: {getattr(ELEDataExtractor, 'total_tokens_used', 0)}, ELE only: {getattr(ELEDataExtractor, 'ele_tokens_used', 0)}")
        
        return unique_extracted

    @staticmethod
    def _is_insect_screen_page(text):
        """ตรวจสอบว่าเป็นหน้า INSECT SCREEN หรือไม่"""
        if not text:
            return False
        
        text_lower = text.lower()
        
        # ตรวจสอบ patterns ที่บ่งบอกว่าเป็นหน้า insect screen
        insect_screen_patterns = [
            'insect screen for sash product',
            'description insect screen for sash product',
            'customer approve\ninsect screen for sash product',
            'insect screen for sash',
            'screen awning window',  
            'awning window' + '.*' + 'screen',  
        ]
        
        for pattern in insect_screen_patterns:
            if pattern in text_lower:
                logger.info(f"Found insect screen pattern: '{pattern}'")
                return True
            
        if 'screen awning' in text_lower:
            logger.info(f"Found Screen Awning pattern - marking as insect screen page")
            return True
        
        # ตรวจสอบเพิ่มเติมจาก table structure
        lines = text.splitlines()
        for line in lines:
            line_lower = line.lower().strip()
            if line_lower == 'insect screen for sash product':
                logger.info(f"Found exact insect screen description line")
                return True
            
        
        return False

    @staticmethod
    def _has_subpanel_indicator(text):
        """ตรวจสอบบานย่อยจาก Description - เข้มงวดมากขึ้น"""
        if not text:
            return False
        
        lines = text.splitlines()
        
        # ค้นหา Description line และตรวจสอบ
        for line in lines:
            if 'Description' in line or 'description' in line:
                # ดึงเนื้อหา Description ออกมา
                desc_content = line.replace('Description', '').replace('description', '').strip()
                
                logger.info(f"Checking Description content: '{desc_content}'")
                
                # **Pattern 1: ตัวเลขในวงเล็บ (2), (3), (4)**
                import re
                number_pattern = re.search(r'\((\d+)\)', desc_content)
                if number_pattern:
                    number = int(number_pattern.group(1))
                    if number >= 2:
                        logger.info(f"Found ({number}) in Description -> USE AI")
                        return True
                
                # **Pattern 2: เครื่องหมาย + ที่บ่งบอกการรวม**
                desc_lower = desc_content.lower()
                plus_indicators = [
                    '+ fixed', '+fixed', 
                    '+ transom', '+transom',
                    '+ awning', '+awning',
                    '+ sidelight', '+sidelight',
                    'casement +', 'sliding +',
                    'window +', 'door +',
                    # เพิ่ม patterns สำหรับตำแหน่ง Left/Right
                    'l+', 'r+',  # L+ และ R+
                    'left+', 'right+',
                    '+l', '+r',  # +L และ +R
                    '+left', '+right',
                    # เพิ่ม patterns ทั่วไปที่มี + ระหว่างคำ
                    'door l+', 'door r+',
                    'casement l+', 'casement r+',
                    'window l+', 'window r+'
                ]
                
                for indicator in plus_indicators:
                    if indicator in desc_lower:
                        logger.info(f"Found '+' combination '{indicator}' in Description -> USE AI")
                        return True
        
        logger.info("No subpanel indicators found in Description -> SKIP AI")
        return False

    # Add this method to ELEDataExtractor class
    @staticmethod
    def _is_valid_reference_context(text, ref, ref_position):
        """
        Check if a reference found in text is in a valid context
        (not part of a product description or other non-reference text)
        """
        # Get surrounding context (50 chars before and after)
        start = max(0, ref_position - 50)
        end = min(len(text), ref_position + len(ref) + 50)
        context = text[start:end].lower()
        
        # Invalid contexts - reference appears in these contexts should be ignored
        invalid_contexts = [
            'giesta sg-d03',
            'sg-d03',
            'sg d03',
            'product',
            'description',
            'giesta out-swing non sill sg d03',
            'out-swing non sill sg d03',
            'customer approve'
        ]
        
        for invalid_context in invalid_contexts:
            if invalid_context in context:
                logger.info(f"Reference {ref} found in invalid context: '{invalid_context}' - IGNORING")
                return False
        
        return True

    @staticmethod
    def _extract_valid_references(text):
        """Extract only valid references, filtering out those in invalid contexts"""
        import re
        
        # Find all potential references with their positions
        pattern = r'\b([WD]A?\d+(?:\.\d+)?|ADD)\b'
        matches = []
        
        for match in re.finditer(pattern, text):
            ref = match.group(1)
            position = match.start()
            
            # Check if this reference is in a valid context
            if ELEDataExtractor._is_valid_reference_context(text, ref, position):
                matches.append(ref)
            else:
                logger.info(f"Filtered out invalid reference: {ref}")
        
        return matches

    # Update the _assess_page_complexity method
    @staticmethod
    def _assess_page_complexity(text, refs=None):
        """Assess page complexity - updated to use valid references only"""
        complexity_score = 0
        reasons = []
        
        text_lower = text.lower()
        
        # Use the new method to extract valid references if refs not provided
        if refs is None:
            refs = ELEDataExtractor._extract_valid_references(text)
        else:
            # Filter the provided refs to ensure they're valid
            valid_refs = []
            for ref in refs:
                # Find the reference in text and check context
                ref_positions = [m.start() for m in re.finditer(r'\b' + re.escape(ref) + r'\b', text)]
                for pos in ref_positions:
                    if ELEDataExtractor._is_valid_reference_context(text, ref, pos):
                        if ref not in valid_refs:
                            valid_refs.append(ref)
                        break
            refs = valid_refs
        
        # Continue with existing logic using the filtered refs
        unique_refs = list(set(refs))
        
        if len(refs) > 1 and len(unique_refs) == len(refs):
            reasons.append(f"multiple unique refs ({len(refs)}) - no sub-panels")
            logger.info(f"Found {len(refs)} unique refs: {refs} - skipping AI")
            return {
                'needs_ai': False,
                'score': 0,
                'reason': ', '.join(reasons)
            }
        
        # Rest of the existing complexity assessment logic...
        if len(refs) > 2:
            complexity_score += 1
            reasons.append(f"{len(refs)} total refs")

        if ELEDataExtractor._has_subpanel_indicator(text):
            complexity_score += 2
            reasons.append("has (2) - subpanels detected")
        
        # Complex descriptions check
        complex_descriptions = [
            'casement + fix', '+ fixed', '+ transom', '+ awning'
        ]
        
        for desc in complex_descriptions:
            if desc in text_lower:
                complexity_score += 1
                reasons.append(f"complex desc: {desc}")
                break
        
        # Multiple tables check
        table_indicators = text.count('panel') + text.count('dimension')
        if table_indicators > 3:
            complexity_score += 1
            reasons.append("multiple tables")
        
        # Long text check
        if len(text.split()) > 500:
            complexity_score += 1
            reasons.append("long text")
        
        needs_ai = complexity_score >= 2
        
        return {
            'needs_ai': needs_ai,
            'score': complexity_score,
            'reason': ', '.join(reasons) if reasons else 'simple'
        }

    @staticmethod
    def _process_ele_basic(ref, text, tables, page_num):
        """Process ELE basic for ref - fixed with proper element type extraction"""
        try:
            logger.info(f"Processing ELE basic for {ref}")
            
            # Use existing function for series
            series = ELEDataExtractor._extract_series_from_page_text(text)
            logger.info(f"Extracted series for {ref}: '{series}'")
            
            # **FIX: Use proper element type extraction**
            element_type = ELEDataExtractor._extract_element_type_from_description(text)
            logger.info(f"Extracted element type for {ref}: '{element_type}'")
            
            # Use existing function for dimensions
            page_data = ELEDataExtractor._extract_dimensions_from_page(text, tables, page_num)
            wo, ho = ELEDataExtractor._get_dimensions_for_ref(ref, page_data, text, tables)
            
            # Check dimensions
            if wo and ho and isinstance(wo, int) and isinstance(ho, int):
                logger.info(f"Found valid dimensions for {ref}: {wo}x{ho}")
                
                return {
                    "Ref": ref,
                    "Ele_Wo": wo,
                    "Ele_Ho": ho,
                    "Element_Type": element_type,
                    "Series": series,
                    "Page": page_num,
                    "Page_Order": 0,
                    "Discovery_Order": 0,
                    "OpenAI_Panels": [],
                    "Has_Panel_Details": False,
                    "Source": "Basic_Processing",
                    "Processing_Type": "Basic"
                }
            else:
                # Try fallback method
                fallback_wo, fallback_ho = ELEDataExtractor._extract_dimensions_fallback(text, ref)
                
                if fallback_wo and fallback_ho:
                    return {
                        "Ref": ref,
                        "Ele_Wo": fallback_wo,
                        "Ele_Ho": fallback_ho,
                        "Element_Type": element_type,
                        "Series": series,
                        "Page": page_num,
                        "Page_Order": 0,
                        "Discovery_Order": 0,
                        "OpenAI_Panels": [],
                        "Has_Panel_Details": False,
                        "Source": "Basic_Processing_Fallback",
                        "Processing_Type": "Basic"
                    }
                else:
                    logger.warning(f"Could not extract dimensions for {ref}")
        
        except Exception as e:
            logger.error(f"Error processing ELE basic for {ref}: {e}")
        
        return None
    
    @staticmethod     
    def _extract_element_type_from_description(text):
        """Extract element type directly from Description preserving original format"""
        if not text:
            return ""
                
        lines = text.splitlines()
        for line in lines:
            if "Description" in line:
                description = line.replace("Description", "").strip()
                description = ELEDataExtractor._clean_description(description)
                
                # Remove "Customer Approve" and its variations from description
                remove_phrases = [
                    'Customer Approve', 'customer approve', 'Customer approve', 
                    'CUSTOMER APPROVE', 'Customer Approved', 'customer approved'
                ]
                
                for phrase in remove_phrases:
                    description = description.replace(phrase, "").strip()
                
                # Clean up any remaining whitespace or special characters
                description = description.strip()
                
                # Check if description is valid after removing unwanted text
                if description and len(description) > 3 and description not in ['-', '']:
                    logger.info(f"Found cleaned description: '{description}'")
                    return description
                
        return ""
    
    @staticmethod
    def _extract_dimensions_fallback(text, ref):
        """Fallback method to extract dimensions from text"""
        try:
            # Look for dimensions near the reference
            lines = text.splitlines()
            ref_line_index = -1
            
            # Find the line containing the reference
            for i, line in enumerate(lines):
                if ref in line:
                    ref_line_index = i
                    break
            
            if ref_line_index == -1:
                return None, None
            
            # Search in the reference line and nearby lines
            search_lines = []
            start_idx = max(0, ref_line_index - 2)
            end_idx = min(len(lines), ref_line_index + 3)
            search_lines = lines[start_idx:end_idx]
            
            wo, ho = None, None
            
            for line in search_lines:
                # Look for patterns like "1200 x 2400" or "W:1200 H:2400"
                dimension_patterns = [
                    r'(\d{3,4})\s*[xX×]\s*(\d{3,4})',  # 1200 x 2400
                    r'W[:\s]*(\d{3,4})\s*H[:\s]*(\d{3,4})',  # W:1200 H:2400
                    r'(\d{3,4})\s*mm\s*[xX×]\s*(\d{3,4})\s*mm',  # 1200mm x 2400mm
                    r'Width[:\s]*(\d{3,4}).*Height[:\s]*(\d{3,4})',  # Width: 1200 Height: 2400
                ]
                
                for pattern in dimension_patterns:
                    match = re.search(pattern, line, re.IGNORECASE)
                    if match:
                        potential_wo = int(match.group(1))
                        potential_ho = int(match.group(2))
                        
                        # Validate dimensions (reasonable ranges)
                        if (300 <= potential_wo <= 6000 and 
                            200 <= potential_ho <= 3000):
                            wo = potential_wo
                            ho = potential_ho
                            logger.info(f"Fallback dimensions found for {ref}: {wo}x{ho} from pattern: {pattern}")
                            return wo, ho
            
            return None, None
            
        except Exception as e:
            logger.error(f"Error in fallback dimension extraction for {ref}: {e}")
            return None, None

    @staticmethod
    def _extract_description_from_page_text(text):
        """Extract description from page text"""
        if not text:
            return ""
        
        lines = text.splitlines()
        for line in lines:
            if "Description" in line:
                description = line.replace("Description", "").strip()
                # Clean description
                cleaned = ELEDataExtractor._clean_description(description)
                return cleaned
        
        return ""

    @staticmethod
    def _extract_element_type_with_openai(text, refs, openai_product_types=None):
        """Enhanced element type extraction with OpenAI priority and proper description parsing"""
        
        # PRIORITY 1: Use OpenAI product types for any reference
        if openai_product_types:
            for ref in refs:
                if ref in openai_product_types:
                    logger.info(f"Using OpenAI element type for {ref}: {openai_product_types[ref]}")
                    return openai_product_types[ref]
        
        # PRIORITY 2: Extract from Description line และแปลงเป็น Product Type ที่เหมาะสม
        lines = text.splitlines()
        for line in lines:
            if "Description" in line:
                description = line.replace("Description", "").strip()
                description = ELEDataExtractor._clean_description(description)
                
                if description and len(description) > 3:
                    # แปลง description เป็น product type
                    product_type = ELEDataExtractor._parse_description_to_product_type(description)
                    logger.info(f"Found description: '{description}' -> '{product_type}'")
                    return product_type
        
        # PRIORITY 3: Fallback based on reference type
        fallback_type = "Door" if any(ref.startswith('D') for ref in refs) else "Window"
        logger.info(f"Using fallback element type: {fallback_type}")
        return fallback_type

    @staticmethod
    def _parse_description_to_product_type(description):
        """แปลง Description จาก ELE เป็น Product Type ที่เหมาะสม"""
        if not description:
            return '-'
        
        desc_lower = description.lower().strip()
        
        # กรองข้อความที่ไม่ต้องการ
        if desc_lower in ['customer approve', 'customer approved', '-', '','Customer approve',' Customer approve']:
            return '-'
        
        # Mapping จาก Description เป็น Product Type
        description_mappings = {
            # Casement Windows
            'sg casement window': 'Single casement window',
            'single casement window': 'Single casement window',
            'casement window': 'Single casement window',
            
            # Fixed Windows
            'fixed window': 'Fix window',
            'fix window': 'Fix window',
            
            # Sliding Windows  
            '2 panels sliding window': '2 panels sliding window',
            '3 panels sliding window': '3 panels sliding window',
            'sliding window': '2 panels sliding window',
            
            # Awning Windows
            'awning window': 'Awning window',
            'screen awning window': 'Awning window',
            
            # Doors
            'giesta out-swing with sill ms p01 d r': 'Out-swing with sill MS P01 D R',
            'out-swing with sill ms p01 d r': 'Out-swing with sill MS P01 D R',
            'giesta out-swing': 'Out-swing',
            'out-swing': 'Out-swing', 
            '2 panels sliding door': '2 panels sliding door',
            '3 panels 3 tracks sliding door': '3 panels 3 tracks sliding door',
            'sliding door': '2 panels sliding door',
            'airflow door': 'Airflow door',
            
            # Special cases
            'insect screen for sash product': 'Insect screen'
        }
        
        # ตรวจสอบ exact match ก่อน
        if desc_lower in description_mappings:
            return description_mappings[desc_lower]
        
        # ตรวจสอบ partial match
        for key, value in description_mappings.items():
            if key in desc_lower:
                return value
        
        # ตรวจสอบ keywords
        if 'casement' in desc_lower:
            return 'Single casement window'
        elif 'fixed' in desc_lower or 'fix' in desc_lower:
            return 'Fix window'
        elif 'sliding' in desc_lower:
            if 'door' in desc_lower:
                if '3 panel' in desc_lower or '3-panel' in desc_lower:
                    return '3 panels 3 tracks sliding door'
                else:
                    return '2 panels sliding door'
            else:
                return '2 panels sliding window'
        elif 'awning' in desc_lower:
            return 'Awning window'
        elif 'door' in desc_lower:
            if 'airflow' in desc_lower:
                return 'Airflow door'
        
        # ถ้าไม่เจอเลย ให้ return description ที่ clean แล้ว
        cleaned = description.strip()
        return cleaned if cleaned and len(cleaned) > 2 else '-'

    @staticmethod
    def _clean_description(description):
        """Enhanced description cleaning - เวอร์ชันที่แก้ไขแล้ว"""
        if not description:
            return description
        
        # แปลงเป็น string และตัดช่องว่าง
        cleaned = str(description).strip()
        
        # ลบ "Customer approve" เฉพาะเมื่ออยู่ต้นข้อความเท่านั้น
        # ป้องกันการลบชื่อผลิตภัณฑ์ที่ถูกต้อง
        prefixes_to_remove = [
            "Customer approve",
            "customer approve", 
            "Customer Approve",
            "CUSTOMER APPROVE",
            "Customer approved",
            "CUSTOMER APPROVED"
        ]
        
        # ลบเฉพาะที่ต้นข้อความ
        for prefix in prefixes_to_remove:
            if cleaned.lower().startswith(prefix.lower()):
                cleaned = cleaned[len(prefix):].strip()
                break
        
        # ลบช่องว่างเกินแต่เก็บเนื้อหาจริง
        cleaned = " ".join(cleaned.split())
        
        # ลบเครื่องหมายวรรคตอนที่ต้นและท้ายเฉพาะที่ไม่ใช่ส่วนของเนื้อหา
        cleaned = cleaned.strip('.,;:-')
        
        # ถ้าผลลัพธ์ว่างเปล่า ให้คืนค่าต้นฉบับ
        if not cleaned or cleaned.isspace():
            return str(description).strip()
        
        return cleaned
    
    @staticmethod
    def _extract_series_from_page_text(text):
        """Extract series from entire page text"""
        if not text:
            return ""
        
        text_upper = text.upper()
        
        # Priority 1: Brand names
        primary_series = ['GIESTA', 'ATIS', 'TOSTEM', 'YKK', 'LIXIL']
        for series in primary_series:
            if series in text_upper:
                logger.info(f"Found primary series in page text: {series}")
                return series
        
        # Priority 2: Check Description line specifically
        lines = text.splitlines()
        for line in lines:
            if "Description" in line:
                line_upper = line.upper()
                
                # Check for Brand names in description line
                for series in primary_series:
                    if series in line_upper:
                        logger.info(f"Found primary series in description: {series}")
                        return series
                
                # Check for WE series
                we_match = re.search(r'WE\s*(\d+)', line_upper)
                if we_match:
                    series = f"WE{we_match.group(1)}"
                    logger.info(f"Found WE series: {series}")
                    return series
                
                # Check for MS, P01 etc.
                series_patterns = [
                    r'\b(MS\d*)\b',
                    r'\b(P\d+)\b', 
                    r'\b([A-Z]{2,3}\d+)\b'
                ]
                
                for pattern in series_patterns:
                    matches = re.findall(pattern, line_upper)
                    if matches:
                        exclude_words = ['THE', 'AND', 'FOR', 'WITH', 'DOOR', 'WINDOW', 'FIXED', 'SLIDING']
                        valid_matches = [m for m in matches if m not in exclude_words]
                        if valid_matches:
                            series = valid_matches[0]
                            logger.info(f"Found other series: {series}")
                            return series
        
        logger.info("No series found in page text")
        return ""
    
    @staticmethod
    def _extract_dimensions_from_page(text, tables, page_num):
        """Extract opening dimensions from page"""
        # Try header table first
        if tables:
            header_wo, header_ho = ELEDataExtractor._extract_from_header_table(tables)
            if header_wo and header_ho:
                return {"opening_wo": header_wo, "opening_ho": header_ho}
        
        # Try text extraction
        text_wo, text_ho = ELEDataExtractor._extract_from_text(text)
        if text_wo and text_ho:
            return {"opening_wo": text_wo, "opening_ho": text_ho}
        
        return {}

    @staticmethod
    def _extract_from_header_table(tables):
        """Extract dimensions from header table"""
        for table in tables[:2]:  # Check first 2 tables
            if not table or len(table) < 2:
                continue
            
            first_row = table[0]
            header_text = " ".join([str(cell or "") for cell in first_row]).upper()
            
            if "OPENING WIDTH" in header_text and "OPENING HEIGHT" in header_text:
                # Find column positions
                opening_width_col = None
                opening_height_col = None
                
                for col_idx, cell in enumerate(first_row):
                    if cell:
                        cell_text = str(cell).upper().strip()
                        if "OPENING" in cell_text and "WIDTH" in cell_text:
                            opening_width_col = col_idx
                        elif "OPENING" in cell_text and "HEIGHT" in cell_text:
                            opening_height_col = col_idx
                
                # Extract values from data row
                if len(table) > 1 and opening_width_col is not None and opening_height_col is not None:
                    data_row = table[1]
                    
                    wo = ELEDataExtractor._extract_dimension_value(
                        data_row[opening_width_col] if opening_width_col < len(data_row) else None
                    )
                    ho = ELEDataExtractor._extract_dimension_value(
                        data_row[opening_height_col] if opening_height_col < len(data_row) else None
                    )
                    
                    if wo and ho:
                        return wo, ho
        
        return None, None

    @staticmethod
    def _extract_from_text(text):
        """Extract opening dimensions from text"""
        lines = text.splitlines()
        opening_width, opening_height = None, None
        
        for line in lines[:30]:  # Check first 30 lines
            line_upper = line.upper()
            
            if "OPENING WIDTH" in line_upper:
                numbers = re.findall(r'\d+', line)
                for num in numbers:
                    val = int(num)
                    if 300 <= val <= 6000:
                        opening_width = val
                        break
            
            if "OPENING HEIGHT" in line_upper:
                numbers = re.findall(r'\d+', line)
                for num in numbers:
                    val = int(num)
                    if 200 <= val <= 3000:
                        opening_height = val
                        break
        
        return opening_width, opening_height

    @staticmethod
    def _extract_dimension_value(cell):
        """Extract dimension value from cell"""
        if not cell:
            return None
        
        numbers = re.findall(r'\d+', str(cell))
        if numbers:
            val = int(numbers[0])
            if 200 <= val <= 6000:
                return val
        return None

    @staticmethod
    def _get_dimensions_for_ref(ref, page_data, text, tables):
        """Get dimensions for specific reference"""
        # Try to find ref-specific dimensions in tables first
        if tables:
            ref_wo, ref_ho = ELEDataExtractor._find_ref_in_tables(ref, tables)
            if ref_wo and ref_ho:
                return ref_wo, ref_ho
        
        # Use page-level dimensions as fallback
        return page_data.get("opening_wo"), page_data.get("opening_ho")

    @staticmethod
    def _find_ref_in_tables(ref, tables):
        """Find reference-specific dimensions in tables"""
        for table in tables:
            if not table:
                continue
            
            for row in table:
                if not row:
                    continue
                
                # Check if this row contains our reference
                for cell in row:
                    if cell and str(cell).strip() == ref:
                        # Look for WO/HO columns in this table
                        return ELEDataExtractor._extract_wo_ho_from_table(table, row)
        
        return None, None

    @staticmethod
    def _extract_wo_ho_from_table(table, ref_row):
        """Extract WO/HO values from table containing reference"""
        # Find header row with WO/HO
        for i, row in enumerate(table):
            if not row:
                continue
            
            row_text = " ".join([str(cell or "") for cell in row]).upper()
            if "WO" in row_text and "HO" in row_text:
                # Find column indices
                wo_col = ho_col = None
                for j, cell in enumerate(row):
                    if cell:
                        cell_text = str(cell).upper().strip()
                        if cell_text == "WO":
                            wo_col = j
                        elif cell_text == "HO":
                            ho_col = j
                
                # Extract values from reference row
                if wo_col is not None and ho_col is not None:
                    wo_val = str(ref_row[wo_col] or "").strip() if wo_col < len(ref_row) else ""
                    ho_val = str(ref_row[ho_col] or "").strip() if ho_col < len(ref_row) else ""
                    
                    wo = ELEDataExtractor._extract_dimension_value(wo_val)
                    ho = ELEDataExtractor._extract_dimension_value(ho_val)
                    
                    return wo, ho
        
        return None, None

# Global function for token savings logging
def log_token_savings():
    """บันทึกการประหยัด tokens"""
    site_tokens = getattr(PDFDataExtractor, 'site_tokens_used', 0)
    ele_tokens = getattr(ELEDataExtractor, 'ele_tokens_used', 0)
    total_tokens = site_tokens + ele_tokens
    
    logger.info("=== TOKEN SAVINGS SUMMARY ===")
    logger.info(f"Site Survey tokens used: {site_tokens}")
    logger.info(f"ELE tokens used: {ele_tokens}")
    logger.info(f"Total tokens used: {total_tokens}")
    logger.info("Items processed without AI were saved from token usage!")
    logger.info("Only complex items with potential sub-panels used AI processing.")

class CombinedDataProcessor:
    """Class to process and combine sub-panel, insect screen and color data"""
    
    def __init__(self):
        pass
    
    def create_combined_comparison(self, site_data, ele_data, sub_panel_results, insect_screen_results, color_results , door_direction_results ,transom_results):
        """Create combined comparison table with all data types including colors - รักษาลำดับที่เจอ"""
        combined_results = []
        
        # Create lookup for easier access
        sub_panel_lookup = self._create_sub_panel_lookup(sub_panel_results)
        insect_screen_lookup = self._create_insect_screen_lookup(insect_screen_results)
        color_lookup = self._create_color_lookup(color_results)
        direction_lookup = self._create_door_direction_lookup(door_direction_results)  # Assuming door_direction_results is already a dict
        transom_results_lookup = self._create_transom_lookup(transom_results)  # Assuming transom_results is already a dict
        
        # รวม references จาก site และ ELE ตามลำดับที่เจอ โดยไม่เรียงตาม alphabetical
        all_refs_with_order = []
        
        # เพิ่ม site references ตามลำดับที่เจอ
        for item in site_data:
            ref = item["Ref"]
            order = item.get("discovery_order", 0)
            all_refs_with_order.append((ref, order, "site"))
        
        # เพิ่ม ELE references ตามลำดับที่เจอ (รวมรหัสซ้ำ)
        for item in ele_data:
            ref = item["Ref"]
            order = item.get("Discovery_Order", 0) + 10000  # เพิ่ม offset เพื่อแยกจาก site
            all_refs_with_order.append((ref, order, "ele"))
        
        # เรียงตามลำดับที่เจอจริง ไม่ใช่ตัวอักษร
        all_refs_with_order.sort(key=lambda x: x[1])
        
        # เก็บ references ที่ประมวลผลแล้ว
        processed_refs = set()
        
        for ref, order, source in all_refs_with_order:
            logger.info(f"  {ref} (order: {order}, source: {source})")
            
            # ถ้าประมวลผล ref นี้แล้ว (สำหรับ site) ให้ข้าม
            if source == "site" and ref in processed_refs:
                continue
                
            # ถ้าเป็น ELE และมี site อยู่แล้ว ให้ข้าม (เพราะประมวลผลตอน site แล้ว)
            if source == "ele" and ref in processed_refs:
                continue
            
            # Get sub-panel data for this reference
            ref_sub_panels = sub_panel_lookup.get(ref, [])
            ref_insect_screen = insect_screen_lookup.get(ref, {})
            ref_color = color_lookup.get(ref, {})
            ref_direction = direction_lookup.get(ref, {})
            ref_transom = transom_results_lookup.get(ref, {})
            
            if ref_sub_panels:
                # Create rows for each sub-panel
                for panel_data in ref_sub_panels:
                    # Only include insect screen and color data in the first panel row
                    is_first_panel = panel_data == ref_sub_panels[0]
                    
                    combined_row = {
                        "Ref": ref,
                        "Panel": f"Panel {panel_data.get('Panel_Index', 1)}",
                        "บานย่อย_Site": panel_data.get("Site_Sub_Panel", "-"),
                        "บานย่อย_ELE": panel_data.get("ELE_Sub_Panel", "-"),
                        "Site_Screen": ref_insect_screen.get("Site_Screen", "-") if is_first_panel else "",
                        "ELE_Screen": ref_insect_screen.get("ELE_Screen", "-") if is_first_panel else "",
                        "Site_Color": ref_color.get("Site_Color", "-") if is_first_panel else "",
                        "ELE_Color": ref_color.get("ELE_Color", "-") if is_first_panel else "",
                        "Transom": ref_transom.get("Status", "-"),
                        "Direction": ref_direction.get("Direction", "-") if is_first_panel else "",
                        "ELE_Direction": ref_direction.get("ELE_Direction", "-") if is_first_panel else "",
                        "Status": self._determine_combined_status(panel_data, ref_insect_screen, ref_color, is_first_panel ,ref_direction,ref_transom),
                        "Notes": self._generate_combined_notes(panel_data, ref_insect_screen, ref_color, is_first_panel ,ref_direction,ref_transom)
                    }
                    combined_results.append(combined_row)
            else:
                # No sub-panels, but may have insect screen and color data
                combined_row = {
                    "Ref": ref,
                    "Panel": "Single Panel",
                    "บานย่อย_Site": "-",
                    "บานย่อย_ELE": "-",
                    "Site_Screen": ref_insect_screen.get("Site_Screen", "-"),
                    "ELE_Screen": ref_insect_screen.get("ELE_Screen", "-"),
                    "Site_Color": ref_color.get("Site_Color", "-"),
                    "ELE_Color": ref_color.get("ELE_Color", "-"),
                    "Transom": ref_transom.get("Status", "-"),
                    "Direction": ref_direction.get("Direction", "-"),
                    "ELE_Direction": ref_direction.get("ELE_Direction", "-"),
                    "Status": self._determine_single_status(ref_insect_screen, ref_color ,ref_direction, ref_transom),
                    "Notes": self._generate_single_notes(ref_insect_screen, ref_color ,ref_direction, ref_transom)
                }
                combined_results.append(combined_row)
            
            processed_refs.add(ref)
        
        return combined_results
    
    def _create_sub_panel_lookup(self, sub_panel_results):
        """Create lookup table for sub-panel results by reference"""
        lookup = {}
        for item in sub_panel_results:
            ref = item.get("Ref")
            if ref not in lookup:
                lookup[ref] = []
            lookup[ref].append(item)
        return lookup
    
    def _create_insect_screen_lookup(self, insect_screen_results):
        """Create lookup table for insect screen results by reference"""
        lookup = {}
        for item in insect_screen_results:
            ref = item.get("Ref")
            lookup[ref] = item
        return lookup
    
    def _create_color_lookup(self, color_results):
        """Create lookup table for color results by reference"""
        lookup = {}
        for item in color_results:
            ref = item.get("Ref")
            lookup[ref] = item
        return lookup
    
    def _create_door_direction_lookup(self, door_direction_results):
        """Create lookup table for door direction results by reference"""
        lookup = {}
        for item in door_direction_results:
            ref = item.get("Ref")
            lookup[ref] = item
        return lookup
    
    def _create_transom_lookup(self, transom_results):
        """Create lookup table for transom results by reference"""
        lookup = {}
        for item in transom_results:
            ref = item.get("Ref")
            lookup[ref] = item
        return lookup
    
    def _determine_combined_status(self, panel_data, insect_screen_data, color_data, is_first_panel ,door_direction_data ,transom_data):
        """Determine overall status combining sub-panel, insect screen and color status"""
        panel_status = panel_data.get("Overall_Status", "")
        screen_status = insect_screen_data.get("Status", "") if is_first_panel else ""
        color_status = color_data.get("Status", "") if is_first_panel else ""
        direction_status =  door_direction_data.get("Status", "") if is_first_panel else "" 
        transom_status = transom_data.get("Status", "") if is_first_panel else ""
        
        # Count mismatches
        mismatches = []
        if "❌" in panel_status:
            mismatches.append("Panel")
        if "❌" in screen_status and is_first_panel:
            mismatches.append("Screen")
        if "❌" in color_status and is_first_panel:
            mismatches.append("Color")
        if "❌" in direction_status and is_first_panel:
            mismatches.append("Direction")
        
        # Determine status based on mismatches
        if mismatches:
            if len(mismatches) == 1:
                return f"❌ {mismatches[0]} Mismatch"
            else:
                return f"❌ {' + '.join(mismatches)} Mismatch"
        
        # Check for matches
        matches = []
        if "✅" in panel_status:
            matches.append("Panel")
        if "✅" in screen_status and is_first_panel:
            matches.append("Screen")
        if "✅" in color_status and is_first_panel:
            matches.append("Color")
        if "✅" in direction_status and is_first_panel:
            matches.append("Direction")
        if "✅" in transom_status and is_first_panel:
            matches.append("Transom")
        
         # Determine status based on matches
        
        if matches:
            if len(matches) >= 2:
                return "✅ Complete Match"
            else:
                return f"✅ {matches[0]} Match"
        
        return "ℹ️ No data"
    
    def _determine_single_status(self, insect_screen_data, color_data, door_direction_data , transom_data):
        """Determine status for single panel items"""
        screen_status = insect_screen_data.get("Status", "")
        color_status = color_data.get("Status", "")
        direction_status =  door_direction_data.get("Status", "")
        transom_status = transom_data.get("Status", "")

        
        mismatches = []
        if "❌" in screen_status:
            mismatches.append("Screen")
        if "❌" in color_status:
            mismatches.append("Color")
        if "❌" in direction_status:
            mismatches.append("Direction")
        if transom_status == "No":
            mismatches.append("Transom")
        
         # Determine status based on mismatches
        
        if mismatches:
            if len(mismatches) == 1:
                return f"❌ {mismatches[0]} Mismatch"
            else:
                return f"❌ {' + '.join(mismatches)} Mismatch"
        
        matches = []
        if "✅" in screen_status:
            matches.append("Screen")
        if "✅" in color_status:
            matches.append("Color")
        
        if matches:
            return "✅ Data Match"
        
        return "ℹ️ No sub-panel data"
    
    def _generate_combined_notes(self, panel_data, insect_screen_data, color_data, is_first_panel, door_direction_data , transom_data):
        """Generate combined notes for the row"""
        notes = []
        
        # Add panel notes
        panel_notes = panel_data.get("Notes", "")
        if panel_notes and panel_notes != "OK":
            notes.append(f"Panel: {panel_notes}")
        
        # Add screen notes (only for first panel)
        if is_first_panel:
            screen_notes = insect_screen_data.get("Notes", "")
            if screen_notes and screen_notes != "OK":
                notes.append(f"Screen: {screen_notes}")
        
        # Add color notes (only for first panel)
        if is_first_panel:
            color_notes = color_data.get("Notes", "")
            if color_notes and color_notes != "OK" and color_notes != "Colors match":
                notes.append(f"Color: {color_notes}")

        if is_first_panel:
            direction_notes = door_direction_data.get("Notes", "")
            if direction_notes and direction_notes != "OK" and direction_notes != "Direction match":
                notes.append(f"Direction: {direction_notes}")

        if is_first_panel:
            transom_notes = transom_data.get("Notes", "")
            if transom_notes and transom_notes not in ["OK", "", "Yes.", "No."]:  # FIXED: Handle Yes/No notes
                notes.append(f"Transom: {transom_notes}")
        
        return " | ".join(notes) if notes else "OK"
    
    def _generate_single_notes(self, insect_screen_data, color_data, door_direction_data , transom_data):
        """Generate notes for single panel items"""
        notes = []
        
        # Add screen notes
        screen_notes = insect_screen_data.get("Notes", "")
        if screen_notes and screen_notes != "OK":
            notes.append(f"Screen: {screen_notes}")
        
        # Add color notes
        color_notes = color_data.get("Notes", "")
        if color_notes and color_notes != "OK" and color_notes != "Colors match":
            notes.append(f"Color: {color_notes}")

        if door_direction_data:
            direction_notes = door_direction_data.get("Notes", "")
            if direction_notes and direction_notes != "OK" and direction_notes != "Direction match":
                notes.append(f"Direction: {direction_notes}")

        if transom_data:
            transom_notes = transom_data.get("Notes", "")   
            if transom_notes and transom_notes not in ["OK", "", "Yes.", "No."]:  # FIXED: Handle Yes/No notes
                notes.append(f"Transom: {transom_notes}")
        
         # If no notes, indicate no sub-panel information
        
        if not notes:
            notes.append("No sub-panel information available")
        
        return " | ".join(notes)

# Keep DataComparator and Flask routes unchanged...
    
class DataComparator:
    """Class to handle data comparison logic with sub-panel integration and enhanced series matching"""

    @staticmethod
    def compare_data_with_sub_panels_integrated(site_data, ele_data, sub_panel_results, color_results, insect_screen_results , door_direction_results, transom_results):
        """Compare Site Survey vs ELE data with sub-panels integrated into main table"""
        results = []
        
        # Create lookups
        color_lookup = {}
        if color_results.get('success') and color_results.get('results'):
            for color_item in color_results['results']:
                ref = color_item.get('Ref')
                color_lookup[ref] = color_item
        
        screen_lookup = {}
        if insect_screen_results.get('success') and insect_screen_results.get('results'):
            for screen_item in insect_screen_results['results']:
                ref = screen_item.get('Ref')
                screen_lookup[ref] = screen_item
        
        sub_panel_lookup = {}
        if sub_panel_results.get('success') and sub_panel_results.get('results'):
            for sub_item in sub_panel_results['results']:
                ref = sub_item.get('Ref')
                if ref not in sub_panel_lookup:
                    sub_panel_lookup[ref] = []
                sub_panel_lookup[ref].append(sub_item)

        direction_lookup = {}
        if door_direction_results and door_direction_results.get('success') and door_direction_results.get('results'):
            for direction_item in door_direction_results['results']:
                ref = direction_item.get('Ref')
                direction_lookup[ref] = direction_item

        transom_results_lookup = {}
        if transom_results and transom_results.get('success') and transom_results.get('results'):
            for transom_item in transom_results['results']:
                ref = transom_item.get('Ref')
                transom_results_lookup[ref] = transom_item   
        
        # Create ELE lookup that handles duplicates
        ele_lookup = {}
        for item in ele_data:
            ref = item["Ref"]
            if ref not in ele_lookup:
                ele_lookup[ref] = []
            ele_lookup[ref].append(item)
        
        # Process site data in discovery order
        for s in site_data:
            ref = s["Ref"]
            ele_items = ele_lookup.get(ref, [])

            # Try partial matching if exact match fails
            if not ele_items:
                partial_match = DataComparator._find_partial_match(ref, ele_data)
                if partial_match:
                    ele_items = [partial_match]

            # Get additional data for this reference
            color_data = color_lookup.get(ref, {})
            screen_data = screen_lookup.get(ref, {})
            sub_panels = sub_panel_lookup.get(ref, [])
            direction_data = direction_lookup.get(ref, {})  # 
            transom_data = transom_results_lookup.get(ref, {})  #

            if not ele_items:
                # Create missing ELE result for opening
                opening_result = DataComparator._create_missing_ele_result(s, color_data, screen_data , direction_data, transom_data)
                results.append(opening_result)
                
                # Add sub-panel rows if they exist
                for sub_panel in sub_panels:
                    # Pass original product type to sub-panel  
                    sub_panel_with_original = dict(sub_panel)
                    sub_panel_with_original["Original_Product_Type"] = s.get("Product Type", "-")
                    sub_result = DataComparator._create_sub_panel_result(ref, sub_panel_with_original, "site_only")
                    results.append(sub_result)
                continue

            # Compare with first ELE item
            ele = ele_items[0]
            
            # Create opening comparison result
            opening_result = DataComparator._create_opening_comparison_result(s, ele, color_data, screen_data , direction_data ,transom_data)
            results.append(opening_result)
            
            # Add sub-panel rows
            for sub_panel in sub_panels:
                # Pass original product type to sub-panel
                sub_panel_with_original = dict(sub_panel)
                sub_panel_with_original["Original_Product_Type"] = s.get("Product Type", "-")
                sub_result = DataComparator._create_sub_panel_result(ref, sub_panel_with_original, "comparison")
                results.append(sub_result)
        
        # Add ELE items that are missing in site
        site_refs = {item["Ref"] for item in site_data}
        
        for item in ele_data:
            if item["Ref"] not in site_refs:
                color_data = color_lookup.get(item["Ref"], {})
                screen_data = screen_lookup.get(item["Ref"], {})
                direction_data = direction_lookup.get(item["Ref"], {})
                transom_data = transom_results_lookup.get(item["Ref"], {})
                
                result = DataComparator._create_missing_site_result(item, color_data, screen_data , direction_data, transom_data)
                results.append(result)

        return results

    @staticmethod
    def _create_opening_comparison_result(site_item, ele_item, color_data, screen_data , direction_data , transom_data):
        """Create comparison result for opening size"""
        # Compare dimensions (tolerance ±15mm)
        wo_match = abs(site_item["Survey_Wo"] - ele_item["Ele_Wo"]) <= 15
        ho_match = abs(site_item["Survey_Ho"] - ele_item["Ele_Ho"]) <= 15
        
        # Compare product type and description
        product_type_match = DataComparator._compare_product_descriptions(
            site_item.get("Product Type", ""), 
            ele_item.get("Element_Type", "")
        )
        
        # Compare series with enhanced matching
        series_match = DataComparator._compare_series(
            site_item.get("Series", ""),
            ele_item.get("Series", "")
        )

        direction_match = True
        site_direction = '-'
        ele_direction = '-'

        if direction_data:
            site_direction = direction_data.get('Site_Direction', '-')
            ele_direction = direction_data.get('ELE_Direction', '-')
            
            logger.info(f"Direction comparison for {site_item.get('Ref')}: Site={site_direction}, ELE={ele_direction}")
            
            # Only compare if both have actual direction data (not '-' or empty)
            if (site_direction and site_direction != '-' and 
                ele_direction and ele_direction != '-'):
                direction_match = site_direction == ele_direction
            elif (site_direction and site_direction != '-' and 
                (not ele_direction or ele_direction == '-')):
                direction_match = False  # Site has direction but ELE doesn't
            elif (ele_direction and ele_direction != '-' and 
                (not site_direction or site_direction == '-')):
                direction_match = False  # ELE has direction but Site doesn't
            else:
                # Both are empty/dash - consider as match
                direction_match = True
    
        transom_match = True
        ele_transom_status = '-'

        if transom_data:
            ele_transom_status = transom_data.get('Status', '-')
            transom_match = True
    
        # Compare colors
        color_match = False
        if color_data.get('Site_Color') and color_data.get('ELE_Color'):
            # Create color info objects for enhanced comparison
            site_color_info = {
                'Color': color_data.get('Site_Color', ''),
                'Color_Code': color_data.get('Site_Color', '')  # Use color as fallback for code
            }
            ele_color_info = {
                'Color': color_data.get('ELE_Color', ''),
                'Color_Code': color_data.get('ELE_Color', '')  # Use color as fallback for code
            }
            
            color_match, _ = DataComparator._compare_color_values(site_color_info, ele_color_info)
        
        elif not color_data.get('Site_Color') and not color_data.get('ELE_Color'):
            # ทั้งสองกั่งไม่มีสี - ถือว่าตรงกัน
            color_match = True
        elif (color_data.get('Site_Color') in ['-', ''] and color_data.get('ELE_Color') in ['-', '']):
            # ทั้งสองกั่งเป็นค่าว่าง - ถือว่าตรงกัน
            color_match = True
        else:
            # หนึ่งกั่งมีสี อีกกั่งไม่มี - ไม่ตรงกัน
            color_match = False
                
        # Compare insect screens
        screen_match = True
        if screen_data.get('Site_Screen') and screen_data.get('ELE_Screen'):
            screen_match = screen_data.get('Site_Screen') == screen_data.get('ELE_Screen')

        # Generate notes
        notes = []
        if not wo_match:
            diff = abs(site_item["Survey_Wo"] - ele_item["Ele_Wo"])
            notes.append(f"Width diff: {diff}mm")
        
        if not ho_match:
            diff = abs(site_item["Survey_Ho"] - ele_item["Ele_Ho"])
            notes.append(f"Height diff: {diff}mm")
            
        if not product_type_match:
            notes.append(f"Type mismatch")
            
        if not series_match:
            notes.append(f"Series mismatch")
            
        if not color_match:
            notes.append(f"Color mismatch")
            
        if not screen_match:
            notes.append(f"Screen mismatch")

        if not direction_match:
            notes.append(f"Direction mismatch")

        if not transom_match:
            notes.append(f"Transom mismatch")

        # Determine overall status - keep simple format
        if wo_match and ho_match and product_type_match and series_match and color_match and screen_match and direction_match and transom_match:
            overall_status = "✅ Perfect Match"
        else:
            errors = []
            if not wo_match or not ho_match:
                errors.append("Size")
            if not product_type_match:
                errors.append("Type")
            if not series_match:
                errors.append("Series")
            if not color_match:
                errors.append("Color")
            if not screen_match:
                errors.append("Screen")
            if not direction_match:
                errors.append("Direction")
            if not transom_match:
                errors.append("Transom")
            
            if len(errors) == 1:
                overall_status = f"❌ {errors[0]} Mismatch"
            else:
                overall_status = f"❌ {' + '.join(errors)} Mismatch"

        return {
            **site_item,
            "Ele_Wo": ele_item["Ele_Wo"],
            "Ele_Ho": ele_item["Ele_Ho"],
            "Element_Type": ele_item.get("Element_Type", "-") if ele_item.get("Element_Type") else "-",
            "Series_ELE": ele_item["Series"],
            "Site_Color": color_data.get('Site_Color', '-'),
            "ELE_Color": color_data.get('ELE_Color', '-'),
            "Site_Screen": screen_data.get('Site_Screen', '-'),
            "ELE_Screen": screen_data.get('ELE_Screen', '-'),
            "ELE_Transom": ele_transom_status,  # **FIX: Add ELE_Transom field**
            "Site_Direction": site_direction,
            "ELE_Direction": ele_direction,
            "Wo_Status": "✅ Match" if wo_match else "❌ Mismatch",
            "Ho_Status": "✅ Match" if ho_match else "❌ Mismatch",
            "Type_Match": "✅ Match" if product_type_match else "❌ Mismatch",
            "Series_Match": "✅ Match" if series_match else "❌ Mismatch",
            "Color_Match": "✅ Match" if color_match else "❌ Mismatch",
            "Screen_Match": "✅ Match" if screen_match else "❌ Mismatch",
            "Transom_Match": "✅ ELE Detection" if ele_transom_status == "Yes" else "ℹ️ No Transom",  # **FIX: Add Transom_Match**
            "Direction_Match": "✅ Match" if direction_match else "❌ Mismatch",
            "Overall_Status": overall_status,
            "Notes": "; ".join(notes) if notes else "OK",
            "Row_Type": "Opening",
            "Description": "Opening size"
        }
    
    @staticmethod
    def _compare_color_values(site_color_info, ele_color_info):
        """ENHANCED: Compare two color values with color code extraction - FIXES [K]Shine Grey = K"""
        if not site_color_info or not ele_color_info:
            return False, "Missing color information"
        
        # Extract color codes using new enhanced method
        site_code = DataComparator._extract_color_code_enhanced(site_color_info)
        ele_code = DataComparator._extract_color_code_enhanced(ele_color_info)
        
        logger.info(f"Color comparison: Site code='{site_code}', ELE code='{ele_code}'")
        
        # PRIMARY: Direct code match (most important)
        if site_code == ele_code:
            return True, f"Color code match: {site_code}"
        
        # SECONDARY: Full color name match (fallback)
        site_name = site_color_info.get('Color', '').strip().lower()
        ele_name = ele_color_info.get('Color', '').strip().lower()
        
        if site_name and ele_name and site_name == ele_name:
            return True, f"Full color name match: {site_color_info.get('Color').strip()}"
        
        # SPECIAL: Handle Giesta default cases
        if (('[g]autumn brown' in site_name or 'autumn brown' in site_name) and 
            ('[g]autumn brown' in ele_name or 'autumn brown' in ele_name)):
            return True, f"Giesta Autumn Brown match"
        
        return False, f"Color mismatch: Site [{site_code}] vs ELE [{ele_code}]"

    @staticmethod
    def _extract_color_code_enhanced(color_info):
        """ENHANCED: Extract single letter color code from color info - FIXES [K]Shine Grey"""
        if not color_info:
            return ""
        
        # Handle both dict and string inputs
        if isinstance(color_info, dict):
            # Try Color_Code field first
            color_code = color_info.get('Color_Code', '').strip().upper()
            color_text = color_info.get('Color', '').strip()
        else:
            # If it's a string, treat as color text
            color_text = str(color_info).strip()
            color_code = color_text.upper()
        
        # If Color_Code is already a single letter, use it
        if len(color_code) == 1 and color_code.isalpha():
            return color_code
        
        # Pattern 1: [K]Shine Grey -> extract K
        bracket_match = re.search(r'\[([A-Z])\]', color_text)
        if bracket_match:
            extracted_code = bracket_match.group(1)
            logger.info(f"Extracted code from bracket pattern: {extracted_code}")
            return extracted_code
        
        # Pattern 2: Already single letter
        if len(color_text) == 1 and color_text.upper().isalpha():
            return color_text.upper()
        
        # Pattern 3: Try Color_Code again with bracket extraction
        bracket_match_code = re.search(r'\[([A-Z])\]', color_code)
        if bracket_match_code:
            return bracket_match_code.group(1)
        
        # Pattern 4: Multiple letters, take first valid color code
        valid_codes = ['T', 'P', 'D', 'W', 'K', 'G', 'U', 'B', 'C', 'F', 'H', 'J']
        
        # Check color_text first
        for char in color_text.upper():
            if char in valid_codes:
                return char
        
        # Check color_code
        for char in color_code:
            if char in valid_codes:
                return char
        
        # Fallback: return first character if it's alphabetic
        if color_code and color_code[0].isalpha():
            return color_code[0]
        elif color_text and color_text[0].isalpha():
            return color_text[0].upper()
        
        return "G"  # Ultimate fallback

    @staticmethod
    def _create_sub_panel_result(ref, sub_panel_data, result_type):
        """Create result row for sub-panel with enhanced component identification"""
        # Extract dimensions from sub-panel strings
        site_panel = sub_panel_data.get("Site_Sub_Panel", "-")
        ele_panel = sub_panel_data.get("ELE_Sub_Panel", "-")
        
        # Parse dimensions from panel strings like "W:400 × H:900"
        site_wo, site_ho = DataComparator._parse_panel_dimensions(site_panel)
        ele_wo, ele_ho = DataComparator._parse_panel_dimensions(ele_panel)
        
        # ENHANCED: Determine actual component type from dimensions and context
        component_type = DataComparator._determine_actual_component_type(
            site_panel, ele_panel, site_wo, site_ho, ele_wo, ele_ho, 
            sub_panel_data.get("Panel_Index", 1)
        )
        
        return {
            "Ref": ref,
            "Series": "-",  # Not applicable for sub-panels
            "Product Type": component_type,  # SHOW ACTUAL COMPONENT TYPE
            "Survey_Wo": site_wo if site_wo else "-",
            "Survey_Ho": site_ho if site_ho else "-",
            "Insect_Screen": "-",  # Not applicable for sub-panels
            "Page": "-",
            "Ele_Wo": ele_wo if ele_wo else "-",
            "Ele_Ho": ele_ho if ele_ho else "-",
            "Element_Type": component_type,  # SHOW ACTUAL COMPONENT TYPE
            "Series_ELE": "-",
            "Site_Color": "-",  # Not applicable for sub-panels
            "ELE_Color": "-",
            "Site_Screen": "-",
            "ELE_Screen": "-",
            "Wo_Status": sub_panel_data.get("Width_Status", "-"),
            "Ho_Status": sub_panel_data.get("Height_Status", "-"),
            "Type_Match": "-",
            "Series_Match": "-",
            "Color_Match": "-",
            "Screen_Match": "-",
            "Overall_Status": sub_panel_data.get("Overall_Status", "Unknown"),
            "Notes": sub_panel_data.get("Notes", ""),
            "Row_Type": "Sub_Panel",  # Identify as sub-panel row
            "Description": component_type,  # Use actual component type
            "Panel_Index": sub_panel_data.get("Panel_Index", 1)
        }
    
    @staticmethod
    def _determine_actual_component_type(site_panel, ele_panel, site_wo, site_ho, ele_wo, ele_ho, panel_index):
        """Determine the actual component type based on context and dimensions"""
        combined_text = f"{site_panel} {ele_panel}".lower()
        
        # Priority 1: Look for specific component keywords in panel descriptions
        component_keywords = {
            'single casement window': 'Single casement window',
            'casement window': 'Casement window', 
            'casement': 'Casement window',
            'fixed window': 'Fix window',
            'fix window': 'Fix window',
            'fixed': 'Fix window',
            'sliding window': 'Sliding window',
            'sliding': 'Sliding window',
            'awning window': 'Awning window', 
            'awning': 'Awning window',
            'transom': 'Transom',
            'sidelight': 'Sidelight',
            'side panel': 'Side panel'
        }
        
        for keyword, component_type in component_keywords.items():
            if keyword in combined_text:
                return component_type
        
        # Fallback
        return f'-'
    
    @staticmethod
    def _parse_panel_dimensions(panel_string):
        """Parse dimensions from panel string like 'W:400 × H:900'"""
        if not panel_string or panel_string == "-":
            return None, None
        
        import re
        
        # Look for width pattern
        width_match = re.search(r'W:(\d+)', panel_string)
        width = int(width_match.group(1)) if width_match else None
        
        # Look for height pattern  
        height_match = re.search(r'H:(\d+)', panel_string)
        height = int(height_match.group(1)) if height_match else None
        
        return width, height
    
    @staticmethod
    def _determine_sub_panel_description(site_panel, ele_panel):
        """Determine the description of sub-panel"""
        combined_text = f"{site_panel} {ele_panel}".lower()
        
        if "casement" in combined_text:
            return "Casement Panel"
        elif "fixed" in combined_text:
            return "Fixed Panel"
        elif "sliding" in combined_text:
            return "Sliding Panel"
        elif "awning" in combined_text:
            return "Awning Panel"
        elif "top" in combined_text:
            return "Top Panel"
        elif "bottom" in combined_text:
            return "Bottom Panel"
        else:
            return f"Panel {site_panel.split()[-1] if site_panel != '-' else '1'}"
    
    @staticmethod
    def _create_missing_ele_result(site_item, color_data, screen_data , direction_data , transom_data):
        """Create result for missing ELE data (opening)"""
        return {
            **site_item,
            "Ele_Wo": "-", 
            "Ele_Ho": "-",
            "Element_Type": "-",
            "Series_ELE": "-",
            "Site_Color": color_data.get('Site_Color', '-'),
            "ELE_Color": color_data.get('ELE_Color', '-'),
            "Site_Screen": screen_data.get('Site_Screen', '-'),
            "ELE_Screen": screen_data.get('ELE_Screen', '-'),
            "ELE_Transom": transom_data.get('Status', '-') if transom_data else '-',  # **FIX**
            "Site_Direction": direction_data.get('Site_Direction', '-') if direction_data else '-',
            "ELE_Direction": direction_data.get('ELE_Direction', '-') if direction_data else '-',
            "Wo_Status": "❌ Missing in ELE",
            "Ho_Status": "❌ Missing in ELE",
            "Series_Match": "❌ Missing data",
            "Type_Match": "❌ Missing data",
            "Color_Match": "❌ Missing data",
            "Screen_Match": "❌ Missing data",
            "Transom_Match": "❌ Missing data",  # **FIX**
            "Direction_Match": "❌ Missing data",
            "Overall_Status": "❌ Missing in ELE",
            "Notes": "ไม่พบข้อมูลใน ELE",
            "Row_Type": "Opening",
            "Description": "Opening size"
        }

    
    @staticmethod
    def _create_missing_site_result(ele_item, color_data, screen_data , direction_data , transom_data):
        """Create result for missing Site data (opening)"""
        return {
            "Ref": ele_item["Ref"],
            "Series": "-",
            "Product Type": "-",
            "Survey_Wo": "-",
            "Survey_Ho": "-",
            "Insect_Screen": "-",
            "Page": "-",
            "Ele_Wo": ele_item["Ele_Wo"],
            "Ele_Ho": ele_item["Ele_Ho"],
            "Element_Type": ele_item["Element_Type"],
            "Series_ELE": ele_item["Series"],
            "Site_Color": color_data.get('Site_Color', '-'),
            "ELE_Color": color_data.get('ELE_Color', '-'),
            "Site_Screen": screen_data.get('Site_Screen', '-'),
            "ELE_Screen": screen_data.get('ELE_Screen', '-'),
            "ELE_Transom": transom_data.get('Status', '-') if transom_data else '-',  # **FIX**
            "Site_Direction": direction_data.get('Site_Direction', '-') if direction_data else '-',
            "ELE_Direction": direction_data.get('ELE_Direction', '-') if direction_data else '-',
            "Wo_Status": "❌ Missing in Site",
            "Ho_Status": "❌ Missing in Site",
            "Series_Match": "❌ Missing data",
            "Type_Match": "❌ Missing data", 
            "Color_Match": "❌ Missing data",
            "Screen_Match": "❌ Missing data",
            "Transom_Match": "❌ Missing data",  # **FIX**
            "Direction_Match": "❌ Missing data",
            "Overall_Status": "❌ Missing in Site",
            "Notes": "ไม่พบข้อมูลใน Site Survey",
            "Row_Type": "Opening",
            "Description": "Opening size"
        }

    @staticmethod
    def _find_partial_match(ref, ele_data):
        """ปิด partial matching - ต้องตรงกันทุกตัวอักษร"""
        return None 
    
    @staticmethod
    def _compare_series(site_series, ele_series):
        """Enhanced series comparison with GIESTA และ ATIS support"""
        site_normalized = str(site_series).upper().strip()
        ele_normalized = str(ele_series).upper().strip()
        
        # ถ้าทั้งคู่เป็น empty
        if not site_normalized or not ele_normalized:
            return site_normalized == ele_normalized
        
        # Direct match
        if site_normalized == ele_normalized:
            return True
        
        # Enhanced series mappings สำหรับ brands และ variations
        series_mappings = {
            'GIESTA': ['GIESTA', 'GIESTA-MS', 'GIESTA-P01', 'MS', 'P01'],
            'ATIS': ['ATIS', 'AT15', 'ATIS15'],
            'TOSTEM': ['TOSTEM', 'TOSTEM-WE'],
            'WE70': ['WE70', 'WE-70', 'TOSTEM-WE70'],
            'WE50': ['WE50', 'WE-50', 'TOSTEM-WE50'],
            'MS': ['MS', 'GIESTA', 'GIESTA-MS'],
            'P01': ['P01', 'GIESTA', 'GIESTA-P01']
        }
        
        # Check mappings both ways
        for key, values in series_mappings.items():
            if (site_normalized == key and ele_normalized in values) or \
               (ele_normalized == key and site_normalized in values) or \
               (site_normalized in values and ele_normalized in values):
                return True
        
        # Check if one contains the other
        if site_normalized in ele_normalized or ele_normalized in site_normalized:
            return True
        
        return False
    
    @staticmethod
    def _compare_product_descriptions(site_type, ele_type):
        """Compare product type from site survey with element type from ELE - ปรับปรุงการเปรียบเทียบ"""
        # Normalize strings for comparison
        site_normalized = str(site_type).lower().strip()
        ele_normalized = str(ele_type).lower().strip()
        
        # ถ้าข้อมูลเป็น empty หรือ default values ให้ถือว่า match
        empty_values = ['', '-', 'n/a', 'na', 'none', 'select..', 'select']
        if site_normalized in empty_values and ele_normalized in empty_values:
            return True
        
        # Direct match
        if site_normalized == ele_normalized:
            return True
        
        # ถ้าข้อมูลใดข้อมูลหนึ่งเป็น empty ให้ถือว่า match (ไม่แสดงเป็นสีแดง)
        if (site_normalized in empty_values) or (ele_normalized in empty_values):
            return True
        
        # Common mappings and variations - เพิ่มรูปแบบที่หลากหลายมากขึ้น
        mappings = {
            # Door variations
            'door': ['door', 'doors', 'ประตู', 'sliding door', 'out-swing door', 'giesta out-swing'],
            'single door': ['door', 'single door', 'ประตูเดี่ยว', 'out-swing'],
            'double door': ['double door', 'french door', 'ประตูคู่'],
            'sliding door': ['sliding door', 'slide door', 'ประตูเลื่อน', '2 panels sliding door', '3 panels 3 tracks sliding door'],
            'bi-fold door': ['bi-fold door', 'bifold door', 'ประตูพับ'],
            'airflow door': ['airflow door', 'airflow', 'ประตูระบายอากาศ'],
            'out-swing door': ['out-swing door', 'out-swing', 'giesta out-swing', 'door'],
            
            # Window variations  
            'window': ['window', 'windows', 'หน้าต่าง'],
            'single window': ['window', 'single window', 'หน้าต่างเดี่ยว', 'single casement window'],
            'sliding window': ['sliding window', 'slide window', 'หน้าต่างเลื่อน', '2 panels 2 tracks sliding window'],
            'awning window': ['awning window', 'awning', 'awnning window', 'หน้าต่างกระต่าย', 'screen awning window'],
            'casement window': ['casement window', 'casement', 'หน้าต่างบานเปิด', 'single casement window'],
            'fixed window': ['fixed window', 'fix window', 'หน้าต่างติดตาย'],
            
            # Generic terms
            'ประตู': ['door', 'doors', 'sliding door', 'out-swing door'],
            'หน้าต่าง': ['window', 'windows', 'casement window', 'sliding window'],
            
            # ELE specific patterns that should match with site survey
            'sg casement window l+fixed we70': ['single casement window +', 'single casement window', 'casement window'],
            'screen awning window(2) we70': ['awnning window', 'awning window'],
            'single casement window lwe70': ['single casement window', 'casement window'],
            'awning window(2) we70': ['awnning window', 'awning window'],
            'fixed window we70': ['fix window', 'fixed window'],
            '2 panels sliding window we70': ['2 panels 2 tracks sliding window'],
            '3 panels 3 tracks sliding door w': ['3 panels 3 tracks sliding door'],
            '2 panels sliding door we70': ['2 panels 2 tracks sliding door'],
            'giesta out-swing with sill ms p01 d r': ['ms-l-p01-lt', 'out-swing door', 'door'],
            'airflow door r': ['airflow door'],
            'insect screen for sash product': ['screen', 'insect screen']  # สำหรับหน้า insect screen
        }
        
        # Check if either term maps to the other
        for key, values in mappings.items():
            key_lower = key.lower()
            values_lower = [v.lower() for v in values]
            
            if site_normalized == key_lower and ele_normalized in values_lower:
                return True
            if ele_normalized == key_lower and site_normalized in values_lower:
                return True
            
            # Check if site or ele contains any of the mapped values
            if site_normalized in values_lower and ele_normalized == key_lower:
                return True
            if ele_normalized in values_lower and site_normalized == key_lower:
                return True
        
        # Check for partial matches (contains) - เป็นการตรวจสอบที่อ่อนแอกว่า
        # ตรวจสอบคำสำคัญ
        site_words = set(site_normalized.split())
        ele_words = set(ele_normalized.split())
        
        key_terms = {'door', 'window', 'ประตู', 'หน้าต่าง', 'sliding', 'fixed', 'awning', 'awnning', 'casement', 'double', 'single'}
        
        # If both have key terms and they overlap
        site_key_terms = site_words.intersection(key_terms)
        ele_key_terms = ele_words.intersection(key_terms)
        
        if site_key_terms and ele_key_terms and site_key_terms.intersection(ele_key_terms):
            return True
        
        # เพิ่มการตรวจสอบ pattern พิเศษ
        special_patterns = [
            ('casement', 'sg casement'),
            ('awning', 'screen awning'),
            ('sliding', '2 panels'),
            ('sliding', '3 panels'),
            ('door', 'giesta'),
            ('airflow', 'airflow door r')
        ]
        
        for pattern1, pattern2 in special_patterns:
            if pattern1 in site_normalized and pattern2 in ele_normalized:
                return True
            if pattern2 in site_normalized and pattern1 in ele_normalized:
                return True
        
        return False

# Add these functions to your main5.py to support in all processing modules

def process_colors(site_path, ele_path):
    """Enhanced wrapper with more frequent checks"""
    try:
        
        logger.info("Starting color processing")
        result = process_colors(site_path, ele_path)
        
    except Exception as e:
        logger.error(f"Error in color processing: {e}")
        return {'success': False, 'results': []}

def process_insect_screens(site_path, ele_path):
    """Wrapper for insect screen processing with support"""
    try:
       
        logger.info("Starting insect screen processing withsupport")
        result = process_insect_screens(site_path, ele_path)
            
        return result
    except Exception as e:
        logger.error(f"Error in insect screen processing: {e}")
        return {'success': False, 'results': []}

def process_sub_panels(site_path, ele_path):
    """ใช้ข้อมูล AI จาก main5.py โดยตรงแทนการใช้ sub_panel_full.py"""
    try:
        logger.info("=== PROCESSING SUB-PANELS WITH AI DATA ===")
        
        # Extract data โดยใช้ AI (ข้อมูลที่มีอยู่แล้วจากการประมวลผลหลัก)
        site_data = PDFDataExtractor.extract_site_survey_data(site_path)
        ele_data = ELEDataExtractor.extract_ele_data(ele_path)
        
        # สร้าง sub-panel results จากข้อมูล AI ที่มีอยู่แล้ว
        sub_panel_results = []
        
        for site_item in site_data:
            ref = site_item["Ref"]
            
            # ดึงข้อมูล panels จาก AI
            site_panels = site_item.get("OpenAI_Panels", [])
            
            # หา ELE item ที่ตรงกัน
            ele_item = None
            for ele in ele_data:
                if ele["Ref"] == ref:
                    ele_item = ele
                    break
            
            ele_panels = []
            if ele_item:
                ele_panels = ele_item.get("OpenAI_Panels", [])
            
            # เฉพาะกรณีที่มีมากกว่า 1 panel
            if len(site_panels) > 1 or len(ele_panels) > 1:
                max_panels = max(len(site_panels), len(ele_panels))
                
                for i in range(max_panels):
                    site_panel = site_panels[i] if i < len(site_panels) else None
                    ele_panel = ele_panels[i] if i < len(ele_panels) else None
                    
                    if site_panel and ele_panel:
                        # เปรียบเทียบขนาด
                        site_panel_str = f"W:{site_panel['width']} × H:{site_panel['height']}"
                        ele_panel_str = f"W:{ele_panel['width']} × H:{ele_panel['height']}"
                        
                        width_match = abs(site_panel['width'] - ele_panel['width']) <= 15
                        height_match = abs(site_panel['height'] - ele_panel['height']) <= 15
                        
                        width_status = "✅ Match" if width_match else "❌ Mismatch"
                        height_status = "✅ Match" if height_match else "❌ Mismatch"
                        
                        if width_match and height_match:
                            overall_status = "✅ Perfect Match"
                            notes = "OK"
                        else:
                            errors = []
                            if not width_match:
                                diff = abs(site_panel['width'] - ele_panel['width'])
                                errors.append(f"Width diff: {diff}mm")
                            if not height_match:
                                diff = abs(site_panel['height'] - ele_panel['height'])
                                errors.append(f"Height diff: {diff}mm")
                            
                            overall_status = "❌ Size Mismatch"
                            notes = "; ".join(errors)
                        
                        sub_panel_results.append({
                            "Ref": ref,
                            "Panel_Index": i + 1,
                            "Site_Sub_Panel": site_panel_str,
                            "ELE_Sub_Panel": ele_panel_str,
                            "Width_Status": width_status,
                            "Height_Status": height_status,
                            "Overall_Status": overall_status,
                            "Notes": notes
                        })
                        
                    elif site_panel and not ele_panel:
                        site_panel_str = f"W:{site_panel['width']} × H:{site_panel['height']}"
                        sub_panel_results.append({
                            "Ref": ref,
                            "Panel_Index": i + 1,
                            "Site_Sub_Panel": site_panel_str,
                            "ELE_Sub_Panel": "-",
                            "Width_Status": "❌ Missing in ELE",
                            "Height_Status": "❌ Missing in ELE",
                            "Overall_Status": "❌ Missing in ELE",
                            "Notes": "บานย่อยไม่พบใน ELE"
                        })
                        
                    elif not site_panel and ele_panel:
                        ele_panel_str = f"W:{ele_panel['width']} × H:{ele_panel['height']}"
                        sub_panel_results.append({
                            "Ref": ref,
                            "Panel_Index": i + 1,
                            "Site_Sub_Panel": "-",
                            "ELE_Sub_Panel": ele_panel_str,
                            "Width_Status": "❌ Missing in Site",
                            "Height_Status": "❌ Missing in Site",
                            "Overall_Status": "❌ Missing in Site",
                            "Notes": "บานย่อยไม่พบใน Site Survey"
                        })
        
        logger.info(f"Sub-panel processing completed: {len(sub_panel_results)} items")
        
        return {
            'success': True,
            'results': sub_panel_results
        }
        
    except Exception as e:
        logger.error(f"Error in process_sub_panels: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return {
            'success': False,
            'error': str(e),
            'results': []
        }
    
def process_door_directions(site_path, ele_path):
    """Wrapper for door direction processing with support"""
    try:
        
        logger.info("Starting door direction processing with support")
        result = process_door_directions(site_path, ele_path)
        
    except Exception as e:
        logger.error(f"Error in door direction processing: {e}")
        return {'success': False, 'results': []}
    
def process_transoms(ele_path):
    """Wrapper for transom processing with support"""
    try:
        logger.info("Starting transom processing with support")
        # ใช้เฉพาะ ELE path อย่างเดียว
        result = process_transoms(ele_path, use_context_detection=False)  # ✅ ถูกต้อง
        return result
    except Exception as e:
        logger.error(f"Error in transom processing: {e}")
        return {'success': False, 'results': []}
    

# Flask Routes
@app.route('/')
def index():
    return render_template('index5.html')
    
# Updated Flask route with complete 
# แก้ไข Flask route /upload ใน main5.py

@app.route('/upload', methods=['POST'])
def upload_files():
    site_path = None
    ele_path = None
    
    def cleanup_files():
        """Helper function to clean up files"""
        nonlocal site_path, ele_path
        try:
            if site_path and os.path.exists(site_path):
                os.remove(site_path)
                logger.info(f"Cleaned up: {site_path}")
        except Exception as e:
            logger.warning(f"Could not remove site file: {e}")
        try:
            if ele_path and os.path.exists(ele_path):
                os.remove(ele_path)
                logger.info(f"Cleaned up: {ele_path}")
        except Exception as e:
            logger.warning(f"Could not remove ele file: {e}")

    try:
        # Validate file uploads
        if 'site_survey' not in request.files or 'ele_file' not in request.files:
            return jsonify({'error': 'กรุณาเลือกไฟล์ทั้งสองไฟล์'}), 400

        site_file = request.files['site_survey']
        ele_file = request.files['ele_file']

        if site_file.filename == '' or ele_file.filename == '':
            return jsonify({'error': 'กรุณาเลือกไฟล์ทั้งสองไฟล์'}), 400

        # Save files securely
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        site_filename = f"{timestamp}_site_{secure_filename(site_file.filename)}"
        ele_filename = f"{timestamp}_ele_{secure_filename(ele_file.filename)}"

        site_path = os.path.join(app.config['UPLOAD_FOLDER'], site_filename)
        ele_path = os.path.join(app.config['UPLOAD_FOLDER'], ele_filename)

        site_file.save(site_path)
        ele_file.save(ele_path)

        logger.info(f"Files saved: {site_filename}, {ele_filename}")

        # Process Site Survey with token tracking
        logger.info("Starting site survey extraction...")
        try:
            site_data = PDFDataExtractor.extract_site_survey_data(site_path)
            logger.info(f"Site survey completed: {len(site_data)} items")
        except Exception as e:
            logger.error(f"Site survey extraction failed: {e}")
            cleanup_files()
            return jsonify({'error': f'เกิดข้อผิดพลาดในการประมวลผล Site Survey: {str(e)}'}), 500

        # Process ELE with token tracking
        logger.info("Starting ELE extraction...")
        try:
            ele_data = ELEDataExtractor.extract_ele_data(ele_path)
            logger.info(f"ELE extraction completed: {len(ele_data)} items")
        except Exception as e:
            logger.error(f"ELE extraction failed: {e}")
            cleanup_files()
            return jsonify({'error': f'เกิดข้อผิดพลาดในการประมวลผล ELE: {str(e)}'}), 500

        if not site_data and not ele_data:
            cleanup_files()
            return jsonify({'error': 'ไม่สามารถดึงข้อมูลจากไฟล์ PDF ได้'}), 400

        # Process colors
        logger.info("Processing colors...")
        try:
            color_results = process_colors(site_path, ele_path)
            logger.info(f"Color processing completed: {len(color_results.get('results', []))} items")
        except Exception as e:
            logger.warning(f"Color processing failed, continuing without colors: {e}")
            color_results = {'success': False, 'results': []}

        # Process insect screens
        logger.info("Processing insect screens...")
        try:
            insect_screen_results = process_insect_screens(site_path, ele_path)
            logger.info(f"Insect screen processing completed: {len(insect_screen_results.get('results', []))} items")
        except Exception as e:
            logger.warning(f"Insect screen processing failed, continuing without screens: {e}")
            insect_screen_results = {'success': False, 'results': []}

        # Process sub-panels
        logger.info("Processing sub-panels...")
        try:
            sub_panel_results = process_sub_panels(site_path, ele_path)  # ใช้ function ที่แก้ไขแล้ว
            logger.info(f"Sub-panel processing completed: {len(sub_panel_results.get('results', []))} items")
        except Exception as e:
            logger.warning(f"Sub-panel processing failed, continuing without sub-panels: {e}")
            sub_panel_results = {'success': False, 'results': []}

        # Process door directions
        logger.info("Processing door directions...")
        try:
            door_direction_results = process_door_directions(site_path, ele_path)
            logger.info(f"Door direction processing completed: {len(door_direction_results.get('results', []))} items")
        except Exception as e:
            logger.warning(f"Door direction processing failed, continuing without directions: {e}")
            door_direction_results = {'success': False, 'results': []}

        # Process transoms
        logger.info("Processing transoms...")
        try:
            transom_results = process_transoms(ele_path)
            logger.info(f"Transom processing completed: {len(transom_results.get('results', []))} items")
        
            if transom_results.get('success') and transom_results.get('results'):
                for transom_item in transom_results['results']:
                    ref = transom_item.get('Ref')
                    status = transom_item.get('Status')
                    site_transom = transom_item.get('Site_Transom', '-')
                    ele_transom = transom_item.get('ELE_Transom', '-')
                    logger.info(f"Transom {ref}: Status={status}, Site={site_transom}, ELE={ele_transom}")
            
        except Exception as e:
            logger.warning(f"Transom processing failed, continuing without transoms: {e}")
            transom_results = {'success': False, 'results': []}

        # Compare data
        logger.info("Creating comparison results...")
        try:
            comparison_results = DataComparator.compare_data_with_sub_panels_integrated(
                site_data, ele_data, sub_panel_results, color_results,
                insect_screen_results, door_direction_results, transom_results
            )
            logger.info(f"Comparison completed: {len(comparison_results)} result rows")
        except Exception as e:
            logger.error(f"Comparison failed: {e}")
            cleanup_files()
            return jsonify({'error': f'เกิดข้อผิดพลาดในการเปรียบเทียบข้อมูล: {str(e)}'}), 500

        # Combined comparison
        logger.info("Creating combined comparison...")
        try:
            combined_processor = CombinedDataProcessor()

            sub_panel_data = sub_panel_results.get('results', []) if sub_panel_results.get('success') else []
            insect_screen_data = insect_screen_results.get('results', []) if insect_screen_results.get('success') else []
            color_data = color_results.get('results', []) if color_results.get('success') else []
            door_direction_data = door_direction_results.get('results', []) if door_direction_results.get('success') else []
            transom_data = transom_results.get('results', []) if transom_results.get('success') else []

            combined_comparison = combined_processor.create_combined_comparison(
                site_data, ele_data, sub_panel_data, insect_screen_data,
                color_data, door_direction_data, transom_data
            )
            logger.info(f"Combined comparison completed: {len(combined_comparison)} rows")
        except Exception as e:
            logger.warning(f"Combined comparison failed, using basic results: {e}")
            combined_comparison = []

        # Calculate summary statistics
        total_items = len(site_data)
        opening_rows = len([r for r in comparison_results if r.get('Row_Type') == 'Opening'])
        sub_panel_rows = len([r for r in comparison_results if r.get('Row_Type') == 'Sub_Panel'])
        main_perfect_matches = len([r for r in comparison_results
                                   if r.get('Row_Type') == 'Opening' and
                                   r.get('Overall_Status') == 'โœ… Perfect Match'])

        status_counts = {}
        for row in comparison_results:
            status = row.get('Overall_Status', 'Unknown')
            if 'โœ…' in status:
                status_type = 'Match'
            elif 'โŒ' in status:
                status_type = 'Mismatch'
            else:
                status_type = 'Other'
            status_counts[status_type] = status_counts.get(status_type, 0) + 1

        # รวบรวม token usage อย่างถูกต้อง
        site_tokens = getattr(PDFDataExtractor, 'site_tokens_used', 0)
        total_tokens = getattr(ELEDataExtractor, 'total_tokens_used', site_tokens)
        ele_tokens = total_tokens - site_tokens
        estimated_cost = calculate_estimated_cost(total_tokens)
        
        # สร้าง token_usage object ที่ครบถ้วน
        final_token_usage = {
            'total_tokens': total_tokens,
            'site_tokens': site_tokens, 
            'ele_tokens': ele_tokens,
            'estimated_cost': estimated_cost,
            'api_enabled': ENABLE_OPENAI and openai_client is not None
        }

        # สร้าง summary object
        summary = {
            'total_items': total_items,
            'main_perfect_matches': main_perfect_matches,
            'main_mismatch_items': opening_rows - main_perfect_matches,
            'main_success_rate': round((main_perfect_matches / opening_rows * 100), 1) if opening_rows > 0 else 0,
            'total_combined_rows': len(comparison_results),
            'opening_rows': opening_rows,
            'sub_panel_rows': sub_panel_rows,
            'status_distribution': status_counts,
            'sub_panel_count': len(sub_panel_data),
            'insect_screen_count': len(insect_screen_data),
            'color_count': len(color_data),
            'door_direction_count': len(door_direction_data),
            'transom_count': len(transom_data)
        }
        
        # Log token usage for debugging
        logger.info(f"=== FINAL TOKEN USAGE ===")
        logger.info(f"Site Survey tokens: {site_tokens}")
        logger.info(f"ELE tokens: {ele_tokens}")
        logger.info(f"Total tokens: {total_tokens}")
        logger.info(f"Estimated cost: ${estimated_cost:.4f}")
        logger.info(f"API enabled: {final_token_usage['api_enabled']}")

        cleanup_files()

        # สร้าง response data พร้อม token_usage
        # ใน route /upload หลังจาก cleanup_files()
        response_data = {
            'success': True,
            'summary': summary,
            'results': comparison_results,
            'site_data': site_data,
            'ele_data': ele_data,
            'combined_comparison': combined_comparison,
            'sub_panel_results': sub_panel_data,
            'insect_screen_results': insect_screen_data,
            'color_results': color_data,
            'door_direction_results': door_direction_data,
            'transom_results': transom_data,
            'token_usage': {
                'total_tokens': total_tokens,
                'site_tokens': site_tokens,
                'ele_tokens': ele_tokens, 
                'estimated_cost': estimated_cost,
                'api_enabled': ENABLE_OPENAI and openai_client is not None
            }
        }

        # เพิ่ม log เพื่อ debug
        logger.info(f"Sending token_usage: {response_data['token_usage']}")

        return jsonify(response_data)

    except Exception as e:
        logger.error(f"Unexpected error in upload_files: {e}")
        import traceback
        logger.error(traceback.format_exc())

        cleanup_files()
        
        # ส่ง token_usage กลับไปแม้จะเกิดข้อผิดพลาด
        fallback_token_usage = {
            'total_tokens': 0,
            'site_tokens': 0,
            'ele_tokens': 0,
            'estimated_cost': 0.0,
            'api_enabled': False
        }
        
        return jsonify({
            'error': f'เกิดข้อผิดพลาดที่ไม่คาดคิด: {str(e)}',
            'token_usage': fallback_token_usage
        }), 500

@app.route('/download_excel', methods=['POST'])
def download_excel():
    try:
        data = request.json
        comparison_results = data.get('results', [])
        site_data = data.get('site_data', [])
        ele_data = data.get('ele_data', [])
        combined_comparison = data.get('combined_comparison', [])
        sub_panel_results = data.get('sub_panel_results', [])
        insect_screen_results = data.get('insect_screen_results', [])
        color_results = data.get('color_results', [])
        door_direction_results = data.get('door_direction_results', [])
        transom_results = data.get('transom_results', [])
        
        # Create Excel file in memory
        output = io.BytesIO()
        
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            # Combined comparison sheet (main table)
            if combined_comparison:
                df_combined = pd.DataFrame(combined_comparison)
                df_combined.to_excel(writer, sheet_name='Combined_Comparison', index=False)
            
            # Main comparison sheet
            if comparison_results:
                df_comparison = pd.DataFrame(comparison_results)
                df_comparison.to_excel(writer, sheet_name='Main_Comparison', index=False)
            
            # Site data sheet
            if site_data:
                df_site = pd.DataFrame(site_data)
                df_site.to_excel(writer, sheet_name='Site_Survey', index=False)
            
            # ELE data sheet
            if ele_data:
                df_ele = pd.DataFrame(ele_data)
                df_ele.to_excel(writer, sheet_name='ELE_Data', index=False)
            
            # Sub-panel detailed sheet
            if sub_panel_results:
                df_sub_panel = pd.DataFrame(sub_panel_results)
                df_sub_panel.to_excel(writer, sheet_name='Sub_Panel_Details', index=False)
            
            # Insect screen detailed sheet
            if insect_screen_results:
                df_insect_screen = pd.DataFrame(insect_screen_results)
                df_insect_screen.to_excel(writer, sheet_name='Insect_Screen_Details', index=False)
            
            # Color detailed sheet
            if color_results:
                df_color = pd.DataFrame(color_results)
                df_color.to_excel(writer, sheet_name='Color_Details', index=False)

            # Door direction detailed sheet
            if door_direction_results:
                df_door_direction = pd.DataFrame(door_direction_results)
                df_door_direction.to_excel(writer, sheet_name='Door_Direction_Details', index=False)

            if transom_results:
                df_transom = pd.DataFrame(transom_results)
                df_transom.to_excel(writer, sheet_name='Transom_Details', index=False)
        
        output.seek(0)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"Enhanced_Analysis_with_OpenAI_{timestamp}.xlsx"
        
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename
        )
        
    except Exception as e:
        logger.error(f"Error creating Excel file: {e}")
        return jsonify({'error': f'เกิดข้อผิดพลาดในการสร้างไฟล์ Excel: {str(e)}'}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000, threaded=True)