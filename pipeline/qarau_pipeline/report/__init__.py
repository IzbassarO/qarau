"""PDF inspection report (RPT1): inputs.py reads the database, render.py builds the PDF."""

from .inputs import ReportInput, load_report_input
from .render import LANGS, generate, load_strings, render_pdf

__all__ = ["LANGS", "ReportInput", "generate", "load_report_input", "load_strings", "render_pdf"]
