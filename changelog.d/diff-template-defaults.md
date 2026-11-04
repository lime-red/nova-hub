### Changed
- **`run.py --diff-template` shows the code's default** for every key a config
  leaves out, beside the template's value, and marks any key where the two
  disagree. A test now holds the template to the code's defaults, so leaving a
  shared key out always means what the template says.
