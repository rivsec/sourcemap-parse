# sourcemap-parse

If a source map is accessible, this script extracts out all of the sources to make them ready for post processing, e.g. in Cursor, grep, or other tools. 

## Features

- Download sourcemap files from URLs
- Extract all source files with proper directory structure
- Preserve original file paths and content

## Installation

1. Clone this repository or download the files
2. Install the required dependencies:

``` bash
pip install -r requirements.txt
```

## Usage
``` bash
python sourcemap-parse.py https://example.com/ --proxy socks5://127.0.0.1:9001 --extract_sources --output_dir C:\\tmp\\extracted_sources
``` 