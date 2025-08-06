# sourcemap-parse

If a source map is accessible, this script extracts out all of the sources to make them ready for post processing, e.g. in Cursor, grep, or other tools.

## Features

- Download sourcemap files from URLs
- Extract all source files with proper directory structure
- Analyze sourcemap structure and metadata
- Handle webpack and other sourcemap formats
- Preserve original file paths and content

## Installation

1. Clone this repository or download the files
2. Install the required dependencies:

```bash
pip install -r requirements.txt
```

## Usage

### Basic Usage

Download and extract all source files from a sourcemap:

```bash
python sourcemap-parse.py <sourcemap_url>
```

### Examples

```bash
# Extract sources to default 'extracted_sources' directory
python sourcemap-parse.py https://example.com/app.js.map

# Extract sources to a custom directory
python sourcemap-parse.py https://example.com/app.js.map --output my_sources

# Analyze sourcemap structure without extracting files
python sourcemap-parse.py https://example.com/app.js.map --analyze
```

### Command Line Options

- `url` (required): URL to the sourcemap file
- `--output, -o`: Output directory for extracted sources (default: `extracted_sources`)
- `--analyze, -a`: Analyze the sourcemap structure without extracting files
- `--help, -h`: Show help message

### Output

The script will:

1. **Download** the sourcemap file from the provided URL
2. **Analyze** the sourcemap structure and display metadata:
   - Version information
   - Number of source files
   - File paths and content availability
   - Mapping information
3. **Extract** all source files (unless `--analyze` flag is used):
   - Creates proper directory structure
   - Handles webpack:// prefixes
   - Preserves original file paths
   - Writes source content to files

### Example Output

```
Downloading sourcemap from: https://example.com/app.js.map
=== Sourcemap Analysis ===
Version: 3
File: app.js
Source Root: 
Number of sources: 15
Number of source contents: 15
First 5 source files:
  ✓ src/components/App.js
  ✓ src/components/Header.js
  ✓ src/utils/helpers.js
  ✓ src/styles/main.css
  ✓ src/index.js
  ... and 10 more
Number of names: 245
Mappings length: 12345 characters
==============================
Found 15 source files to extract
Extracted: extracted_sources/src/components/App.js
Extracted: extracted_sources/src/components/Header.js
...
Extraction complete! 15 files extracted to 'extracted_sources' directory
```

## Supported Sourcemap Formats

- Standard sourcemap v3 format
- Webpack sourcemaps (handles `webpack://` prefixes)
- Sourcemaps with embedded source content
- Various file path formats

## Error Handling

The script handles common errors gracefully:
- Network connection issues
- Invalid JSON in sourcemap files
- Missing source content
- File system permission errors

## Requirements

- Python 3.6+
- `requests` library (>=2.25.0) 
