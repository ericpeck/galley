"""Document size thresholds shared by the window and the editors.

These used to live in ``main_window.py``. The editor gates syntax colors on
``JSON_BUSY_BYTES`` too, and ``main_window`` imports the editor, so the
numbers moved here to keep the imports one-way.

Final size thresholds, in bytes. 1 KB is 1000 bytes and 1 MB is 1_000_000
bytes, the same SI units the size warning shows. YAML busy and progress
limits depend on the document: a tab takes the pure parser, so those
documents reach each state at a smaller size. The warning limit is the same
either way.
"""

_SI_KB = 1000
_SI_MB = 1_000_000
YAML_BUSY_BYTES = 192 * _SI_KB
YAML_TAB_BUSY_BYTES = 96 * _SI_KB
YAML_PROGRESS_BYTES = 704 * _SI_KB
YAML_TAB_PROGRESS_BYTES = 352 * _SI_KB
YAML_WARNING_BYTES = 352 * _SI_KB
JSON_BUSY_BYTES = 5 * _SI_MB
JSON_PROGRESS_BYTES = 20 * _SI_MB
JSON_WARNING_BYTES = 100 * _SI_MB
# Convert from Python runs on the UI thread with no busy indicator. Above
# these UTF-8 sizes of Input, it is refused so the window stays responsive.
# The size bench rows ``python_to_json`` and ``python_to_yaml`` time them.
PYTHON_TO_JSON_BYTES = 1 * _SI_MB
PYTHON_TO_YAML_BYTES = YAML_BUSY_BYTES
