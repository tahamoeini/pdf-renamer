import os
import re
import fitz  # PyMuPDF for better text extraction
import unicodedata
import logging
from pathlib import Path
from collections import defaultdict
from PyPDF2 import PdfReader

# Configure logging for debugging and tracking
logging.basicConfig(filename='pdf_renaming.log', level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Maximum filename length (Windows: 255, Linux/macOS: ~255)
MAX_FILENAME_LENGTH = 100  

def normalize_text(text: str) -> str:
    """Normalize text and remove unnecessary special characters."""
    if not text:
        return "Untitled"

    text = unicodedata.normalize('NFKD', text).strip()  # Normalize Unicode characters
    text = re.sub(r'[^\w\s\-.,:;()&%/|+_]', '', text)  # Keep only valid filename characters
    text = re.sub(r'\s+', ' ', text).strip()  # Remove extra spaces

    return text if len(text) > 3 else "Untitled"

def get_pdf_title_metadata(pdf_path: Path) -> str:
    """Extract title from PDF metadata."""
    try:
        reader = PdfReader(pdf_path)
        metadata = reader.metadata
        title = metadata.get('/Title', '')

        # Remove common irrelevant titles
        if title and not any(phrase in title.lower() for phrase in ["paper title", "draft", "untitled"]):
            return normalize_text(title)
    except Exception as e:
        logging.error(f"Error reading metadata from {pdf_path}: {e}")

    return None

def get_pdf_title_text(pdf_path: Path) -> str:
    """Extract the first meaningful text from the PDF."""
    try:
        doc = fitz.open(pdf_path)
        for page_num in range(min(len(doc), 3)):  # Scan the first 3 pages
            text = doc.load_page(page_num).get_text("text")
            if text:
                for line in text.split("\n"):
                    cleaned_line = normalize_text(line)
                    if len(cleaned_line) > 4 and not re.match(r'^\d{4}$', cleaned_line):  # Avoid year-only titles
                        doc.close()
                        return cleaned_line
        doc.close()
    except Exception as e:
        logging.error(f"Error extracting text from {pdf_path}: {e}")

    return None

def get_pdf_title_using_regex(pdf_path: Path) -> str:
    """Use regex to extract a title from the PDF."""
    try:
        doc = fitz.open(pdf_path)
        for page_num in range(min(len(doc), 3)):
            text = doc.load_page(page_num).get_text("text")
            if text:
                title_pattern = r"^[A-Za-z0-9\s\-_&,:;.!?()]+$"
                for line in text.split("\n"):
                    cleaned_line = normalize_text(line)
                    if re.match(title_pattern, cleaned_line) and len(cleaned_line) > 5:
                        doc.close()
                        return cleaned_line
        doc.close()
    except Exception as e:
        logging.error(f"Error using regex on {pdf_path}: {e}")

    return None

def extract_best_title(pdf_path: Path) -> str:
    """Determine the best title from metadata, text, or regex."""
    title = get_pdf_title_metadata(pdf_path)
    if title:
        return title

    title = get_pdf_title_text(pdf_path)
    if title:
        return title

    title = get_pdf_title_using_regex(pdf_path)
    if title:
        return title

    return pdf_path.stem  # Default to filename if all else fails

def sanitize_filename(title: str) -> str:
    """Ensure filenames are valid and do not cut off mid-word."""
    title = re.sub(r'[<>:"/\\|?*]', '_', title)  # Replace invalid characters

    if len(title) > MAX_FILENAME_LENGTH:
        words = title.split()
        new_title = ""
        for word in words:
            if len(new_title) + len(word) + 1 <= MAX_FILENAME_LENGTH:
                new_title += f"{word} "
            else:
                break
        title = new_title.strip()  # Trim at a full word boundary

    return title

def rename_pdf(pdf_path: Path, existing_titles: defaultdict) -> Path:
    """Rename the PDF while avoiding duplicate filenames."""
    title = extract_best_title(pdf_path)
    safe_title = sanitize_filename(title)

    if not safe_title or safe_title.lower() == "untitled":
        return pdf_path  # Don't rename if the title is invalid

    base_name = f"{safe_title}.pdf"
    counter = existing_titles[base_name]
    existing_titles[base_name] += 1

    if counter > 0:
        base_name = f"{safe_title}_{counter}.pdf"

    return pdf_path.parent / base_name

def rename_pdfs_in_directory(directory: str):
    """Process and rename all PDFs in a directory."""
    directory = Path(directory)

    if not directory.exists():
        logging.error(f"Error: Directory {directory} does not exist.")
        return

    pdf_files = list(directory.rglob("*.pdf"))  # Search PDFs recursively
    existing_titles = defaultdict(int)

    for pdf_file in pdf_files:
        if not pdf_file.exists():
            logging.warning(f"Skipping {pdf_file}: File not found.")
            continue

        print(f"Processing: {pdf_file.name}")
        new_pdf_path = rename_pdf(pdf_file, existing_titles)

        if new_pdf_path != pdf_file and not new_pdf_path.exists():
            try:
                os.rename(pdf_file, new_pdf_path)
                print(f"Renamed: {pdf_file.name} -> {new_pdf_path.name}")
            except Exception as e:
                logging.error(f"Error renaming {pdf_file.name}: {e}")
        else:
            print(f"Skipping: {pdf_file.name} (No change or already exists)")

# Example usage
directory = "./"  # Change this to your folder path
rename_pdfs_in_directory(directory)
