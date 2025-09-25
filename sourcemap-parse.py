# Accept URL from user pointing to a sourcemap file and download it temporary
import argparse
import requests
import tempfile
import os
import json
import pathlib
import shutil
import logging
import sys
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import re
import asyncio
import aiohttp
import urllib3

# Disable SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def get_script_tags(url, proxy=None):
    """
    Get all script tags from the <head> element of a webpage.

    Args:
        url (str): The URL of the webpage to check
        proxy (str, optional): Proxy URL (e.g., 'http://proxy:port' or 'socks5://proxy:port')

    Returns:
        list: List of script tag dictionaries with 'src' and 'tag' keys
    """
    try:
        # Make request to the webpage
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 River Security"
        }

        # Configure proxy if provided
        proxies = None
        if proxy:
            proxies = {"http": proxy, "https": proxy}

        response = requests.get(
            url, headers=headers, timeout=10, proxies=proxies, verify=False
        )
        response.raise_for_status()

        # Parse HTML with BeautifulSoup
        soup = BeautifulSoup(response.text, "html.parser")

        # Find the head element
        head = soup.find("head")
        if not head:
            logging.info("No <head> element found")

        # After checking <head>, also check the rest of the document for <script> tags
        # that are not in <head> (e.g., in <body> or elsewhere).
        # We'll collect all <script> tags from the document, then filter out those already in head.
        all_script_tags = soup.find_all("script")

        scripts = []
        for script in all_script_tags:
            src = script.get("src")
            if src:
                # Convert relative URLs to absolute URLs
                if not src.startswith(("http://", "https://")):
                    src = urljoin(url, src)
                parsed_url = urlparse(url)
                parsed_src = urlparse(src)
                if parsed_url.netloc == parsed_src.netloc:
                    scripts.append({"src": src, "tag": str(script)})
                else:
                    logging.info(f"Skipping script from different domain: {src}")

        return scripts

    except requests.RequestException as e:
        logging.error(f"Error fetching {url}: {e}")
        return []
    except Exception as e:
        logging.error(f"Error parsing HTML: {e}")
        return []


def setup_logging(log_level):
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stderr)],
    )


async def check_for_sourcemaps(script_urls, proxy=None):
    """
    Async version of check_for_sourcemaps that processes URLs concurrently.
    """
    results = {}

    # If proxy is provided, we'll use synchronous requests for better proxy support
    if proxy:
        logging.info(
            "Proxy detected, using synchronous requests for better compatibility"
        )
        return check_for_sourcemaps_sync(script_urls, proxy)

    # Create SSL context that doesn't verify certificates
    import ssl

    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    # Create connector with SSL context
    connector = aiohttp.TCPConnector(ssl=ssl_context)

    async with aiohttp.ClientSession(connector=connector) as session:
        # Create tasks for all script URLs
        tasks = [
            check_single_script_async(session, script_url, proxy)
            for script_url in script_urls
        ]

        # Execute all tasks concurrently
        completed_tasks = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results
        for script_url, result in zip(script_urls, completed_tasks):
            if isinstance(result, Exception):
                logging.error(f"Error processing {script_url}: {result}")
                results[script_url] = []
            else:
                results[script_url] = result

    return results


def check_for_sourcemaps_sync(script_urls, proxy=None):
    """
    Synchronous fallback for proxy support when aiohttp proxy handling fails.
    """
    results = {}

    # Configure proxy if provided
    proxies = None
    if proxy:
        proxies = {"http": proxy, "https": proxy}

    for script_url in script_urls:
        logging.info(f"Checking: {script_url}")
        sourcemaps = []

        # Method 1: Check for source map comment in script content
        try:
            response = requests.get(
                script_url, timeout=10, proxies=proxies, verify=False
            )
            if response.status_code == 200:
                # Look for source map comment
                sourcemap_comment = find_sourcemap_comment(response.text)
                if sourcemap_comment:
                    sourcemap_url = urljoin(script_url, sourcemap_comment)
                    if check_url_exists(sourcemap_url, proxy):
                        sourcemaps.append(
                            {
                                "url": sourcemap_url,
                                "method": "comment",
                                "comment": sourcemap_comment,
                            }
                        )
        except Exception as e:
            logging.error(f"  Error checking script content: {e}")

        # Method 2: Try common source map URL patterns
        common_sourcemaps = check_common_sourcemap_patterns(script_url, proxy)
        for sourcemap in common_sourcemaps:
            if sourcemap not in [s["url"] for s in sourcemaps]:  # Avoid duplicates
                sourcemaps.append({"url": sourcemap, "method": "pattern"})

        results[script_url] = sourcemaps

    return results


async def check_single_script_async(session, script_url, proxy=None):
    """
    Async version of checking a single script for sourcemaps.
    """
    logging.info(f"Checking: {script_url}")
    sourcemaps = []

    # Method 1: Check for source map comment in script content
    try:
        # Configure proxy for this request if provided
        request_kwargs = {"timeout": aiohttp.ClientTimeout(total=10)}
        if proxy:
            request_kwargs["proxy"] = proxy

        async with session.get(script_url, **request_kwargs) as response:
            if response.status == 200:
                content = await response.text()
                sourcemap_comment = find_sourcemap_comment(content)
                if sourcemap_comment:
                    sourcemap_url = urljoin(script_url, sourcemap_comment)
                    if await check_if_exists_and_is_map(session, sourcemap_url, proxy):
                        sourcemaps.append(
                            {
                                "url": sourcemap_url,
                                "method": "comment",
                                "comment": sourcemap_comment,
                            }
                        )
    except Exception as e:
        logging.error(f"  Error checking script content: {e}")

    # Method 2: Try common source map URL patterns
    common_sourcemaps = await check_common_sourcemap_patterns_async(
        session, script_url, proxy
    )
    for sourcemap in common_sourcemaps:
        if sourcemap not in [s["url"] for s in sourcemaps]:
            sourcemaps.append({"url": sourcemap, "method": "pattern"})

    return sourcemaps


async def check_common_sourcemap_patterns_async(session, script_url, proxy=None):
    """
    Async version of checking common sourcemap patterns.
    """
    parsed_url = urlparse(script_url)
    path = parsed_url.path
    base_path = path.rsplit(".", 1)[0] if "." in path else path

    patterns = [
        f"{base_path}.map",
        f"{base_path}.js.map",
        f"{base_path}.css.map",
        f"{path}.map",
        f"{path}.js.map",
        f"{path}.css.map",
    ]

    accessible_sourcemaps = []
    for pattern in patterns:
        sourcemap_url = f"{parsed_url.scheme}://{parsed_url.netloc}{pattern}"
        if await check_if_exists_and_is_map(session, sourcemap_url, proxy):
            accessible_sourcemaps.append(sourcemap_url)

    return accessible_sourcemaps


async def check_if_exists_and_is_map(session, url, proxy=None):
    """
    Async version of checking if a URL exists and is a valid sourcemap.
    """
    try:
        # Configure proxy for this request if provided
        request_kwargs = {"timeout": aiohttp.ClientTimeout(total=5)}
        if proxy:
            request_kwargs["proxy"] = proxy

        async with session.get(url, **request_kwargs) as response:
            if response.status == 200:
                # Force JSON processing by reading as text first
                try:
                    text_content = await response.text()
                    data = json.loads(text_content)
                    return (
                        isinstance(data, dict)
                        and "version" in data
                        and "file" in data
                        and "mappings" in data
                    )
                except json.JSONDecodeError as json_error:
                    logging.debug(f"Failed to parse JSON from {url}: {json_error}")
                    return False
        return False
    except Exception as e:
        logging.debug(f"Not a source map: {url}: {e}")
        return False


def find_sourcemap_comment(script_content):
    """
    Find source map comment in script content.

    Args:
        script_content (str): The script content to search

    Returns:
        str or None: Source map URL from comment if found
    """
    patterns = [
        r"//# sourceMappingURL=([^\s]+)",
        r"//@ sourceMappingURL=([^\s]+)",
        r"/\*# sourceMappingURL=([^\s]+) \*/",
        r"/\*@ sourceMappingURL=([^\s]+) @\*/",
    ]

    for pattern in patterns:
        match = re.search(pattern, script_content)
        if match:
            return match.group(1)

    return None


def check_common_sourcemap_patterns(script_url, proxy=None):
    """
    Check common source map URL patterns for a script.

    Args:
        script_url (str): The script URL
        proxy (str, optional): Proxy URL (e.g., 'http://proxy:port' or 'socks5://proxy:port')

    Returns:
        list: List of accessible source map URLs
    """
    parsed_url = urlparse(script_url)
    path = parsed_url.path
    base_path = path.rsplit(".", 1)[0] if "." in path else path

    # Common source map patterns
    patterns = [
        f"{base_path}.map",
        f"{base_path}.js.map",
        f"{base_path}.css.map",
        f"{path}.map",
        f"{path}.js.map",
        f"{path}.css.map",
    ]

    accessible_sourcemaps = []
    for pattern in patterns:
        sourcemap_url = f"{parsed_url.scheme}://{parsed_url.netloc}{pattern}"
        if check_url_exists(sourcemap_url, proxy):
            accessible_sourcemaps.append(sourcemap_url)

    return accessible_sourcemaps


def check_url_exists(url, proxy=None):
    """
    Check if a URL exists by making a GET request and validating it's a source map.

    Args:
        url (str): URL to check
        proxy (str, optional): Proxy URL (e.g., 'http://proxy:port' or 'socks5://proxy:port')

    Returns:
        bool: True if URL exists and is a valid source map
    """
    try:
        # Configure proxy if provided
        proxies = None
        if proxy:
            proxies = {"http": proxy, "https": proxy}

        response = requests.get(url, timeout=5, proxies=proxies, verify=False)
        data = response.json()
        if not (
            isinstance(data, dict)
            and "version" in data
            and "file" in data
            and "mappings" in data
        ):
            return False
        return True
    except Exception as e:
        logging.info(f"Not a source map: {url}: {e}")
        return False


def check_and_clean_output_directory(output_dir):
    """Check if output directory is empty, ask user for confirmation if not"""
    output_path = pathlib.Path(output_dir)

    # Check if directory exists and has contents
    if output_path.exists() and any(output_path.iterdir()):
        print(f"\nWarning: The output directory '{output_dir}' is not empty.")
        response = (
            input("Do you want to continue and delete existing contents? (y/N): ")
            .strip()
            .lower()
        )

        if response in ["y", "yes"]:
            print(f"Cleaning directory '{output_dir}'...")
            # Remove all contents but keep the directory
            for item in output_path.iterdir():
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)
            print("Directory cleaned successfully.")
            return True
        else:
            print("Operation cancelled by user.")
            return False

    return True


def download_sourcemap(url):
    """Download sourcemap from URL and return as JSON"""
    with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
        response = requests.get(url, verify=False)
        response.raise_for_status()
        tmp_file.write(response.content)
        temp_filename = tmp_file.name

    # Load the file as JSON
    with open(temp_filename, "r", encoding="utf-8") as f:
        sourcemap_json = json.load(f)

    # Delete the temporary file
    os.remove(temp_filename)
    return sourcemap_json


def load_sourcemap_from_file(file_path):
    """Load sourcemap from local file and return as JSON"""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            sourcemap_json = json.load(f)
        return sourcemap_json
    except FileNotFoundError:
        raise FileNotFoundError(f"Sourcemap file not found: {file_path}")
    except json.JSONDecodeError as e:
        raise json.JSONDecodeError(
            f"Invalid JSON in sourcemap file {file_path}: {e}", e.doc, e.pos
        )
    except Exception as e:
        raise Exception(f"Error reading sourcemap file {file_path}: {e}")


def extract_source_files(sourcemap_json, output_dir="extracted_sources"):
    """Extract all source files from sourcemap into a folder structure"""

    # Create output directory
    output_path = pathlib.Path(output_dir)
    output_path.mkdir(exist_ok=True)

    # Get sources and sourcesContent from sourcemap
    sources = sourcemap_json.get("sources", [])
    sources_content = sourcemap_json.get("sourcesContent", [])

    if not sources:
        print("No sources found in sourcemap")
        # The sources could be available directly by request. Attempt to get them from the server directly
        for source in sources:
            try:
                response = requests.get(source, verify=False)
                response.raise_for_status()
                sources_content.append(response.text)
            except Exception as e:
                print(f"Error fetching source {source}: {e}")

    if not sources_content:
        print("No source content found in sourcemap!")
        return

    # Ensure we have matching arrays
    if len(sources) != len(sources_content):
        print(
            f"Warning: Mismatch between sources ({len(sources)}) and sourcesContent ({len(sources_content)})"
        )
        return

    print(f"Found {len(sources)} source files to extract")

    extracted_files = []

    for i, (source_path, source_content) in enumerate(zip(sources, sources_content)):
        if source_content is None:
            print(f"Skipping {source_path} - no content available")
            continue

        # Clean up the source path
        # Remove webpack:// or other prefixes
        clean_path = source_path
        if clean_path.startswith("webpack:///"):
            clean_path = clean_path[11:]  # Remove "webpack:///"
        elif clean_path.startswith("webpack://"):
            clean_path = clean_path[10:]  # Remove "webpack://"

        # Handle absolute paths that might start with /
        if clean_path.startswith("/"):
            clean_path = clean_path[1:]

        # Sanitize the path to prevent directory traversal and invalid characters
        # Split the path and filter out any '..' or '.' components
        path_parts = clean_path.split("/")
        sanitized_parts = []
        for part in path_parts:
            if part not in ["..", "."] and part.strip():
                # Remove or replace Windows-invalid characters
                sanitized_part = part
                # Replace invalid characters with underscores
                invalid_chars = '<>:"|?*\\'
                for char in invalid_chars:
                    sanitized_part = sanitized_part.replace(char, "_")
                # Remove any remaining control characters
                sanitized_part = "".join(
                    char for char in sanitized_part if ord(char) >= 32
                )
                # Ensure the part is not empty after sanitization
                if sanitized_part.strip():
                    sanitized_parts.append(sanitized_part)

        # Reconstruct the sanitized path
        clean_path = "/".join(sanitized_parts)

        # Create the full file path
        file_path = output_path / clean_path

        # Check if path is too long for Windows (260 character limit)
        if len(str(file_path)) > 250:  # Leave some buffer
            print(f"Path too long, using fallback filename for: {clean_path}")
            fallback_filename = f"source_{i:04d}.js"
            file_path = output_path / fallback_filename

        # Ensure the directory exists
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            print(f"Error creating directory for {file_path}: {e}")
            # Try to create a sanitized filename as fallback
            try:
                # Create a simple filename based on the index
                fallback_filename = f"source_{i:04d}.js"
                file_path = output_path / fallback_filename
                print(f"Using fallback filename: {fallback_filename}")
            except Exception as fallback_error:
                print(f"Error creating fallback filename: {fallback_error}")
                continue

        # Write the source content to file
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(source_content)
            extracted_files.append(str(file_path))
            print(f"Extracted: {file_path}")
        except Exception as e:
            print(f"Error writing {file_path}: {e}")

    print(
        f"\nExtraction complete! {len(extracted_files)} files extracted to '{output_dir}' directory"
    )
    return extracted_files


def analyze_sourcemap(sourcemap_json):
    """Analyze and display information about the sourcemap"""
    print("=== Sourcemap Analysis ===")

    # Basic info
    version = sourcemap_json.get("version", "unknown")
    file = sourcemap_json.get("file", "unknown")
    source_root = sourcemap_json.get("sourceRoot", "")

    print(f"Version: {version}")
    print(f"File: {file}")
    print(f"Source Root: {source_root}")

    # Sources info
    sources = sourcemap_json.get("sources", [])
    sources_content = sourcemap_json.get("sourcesContent", [])

    print(f"Number of sources: {len(sources)}")
    print(f"Number of source contents: {len(sources_content)}")

    # Show first few sources
    if sources:
        print("\nFirst 5 source files:")
        for i, source in enumerate(sources[:5]):
            has_content = (
                "✓" if i < len(sources_content) and sources_content[i] else "✗"
            )
            print(f"  {has_content} {source}")

        if len(sources) > 5:
            print(f"  ... and {len(sources) - 5} more")

    # Names info
    names = sourcemap_json.get("names", [])
    print(f"Number of names: {len(names)}")

    # Mappings info (just length, not content)
    mappings = sourcemap_json.get("mappings", "")
    print(f"Mappings length: {len(mappings)} characters")

    print("=" * 30)


def main():
    # Parse arguments first
    parser = argparse.ArgumentParser(
        description="Extract source code from sourcemap files. Can either scan a webpage for sourcemaps or process a local sourcemap file directly."
    )
    parser.add_argument(
        "url",
        nargs="?",
        help="URL to the sourcemap file or webpage to scan for sourcemaps",
    )

    parser.add_argument(
        "--map_file",
        "-m",
        help="Path to a local sourcemap file to extract from (alternative to URL)",
    )

    parser.add_argument(
        "--json",
        "-j",
        action="store_true",
        help="Output the sourcemap as JSON array",
    )
    ## Add argument to set log level
    parser.add_argument(
        "--log_level",
        "-l",
        help="Set the log level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
    )

    parser.add_argument(
        "--proxy",
        "-p",
        help="Proxy URL (e.g., 'http://proxy:port' or 'socks5://proxy:port')",
    )

    # Require a output folder directory where files will be extracted to . Only required if --extract_sources is used
    # Argument group for extract_sources
    extract_sources_group = parser.add_argument_group("Extract Sources")
    extract_sources_group.add_argument(
        "--extract_sources",
        "-e",
        action="store_true",
        help="Extract the source files from the sourcemap",
    )
    extract_sources_group.add_argument(
        "--output_dir",
        "-o",
        help="Output directory where files will be extracted to",
    )

    args = parser.parse_args()

     # Set log level
    setup_logging(args.log_level)

    # Validate arguments
    if not args.url and not args.map_file:
        logging.error("Either URL or --map_file must be provided")
        parser.print_help()
        return

    if args.url and args.map_file:
        logging.error("Cannot use both URL and --map_file. Please choose one.")
        parser.print_help()
        return

    if args.extract_sources and not args.output_dir:
        logging.error("Output directory is required when using --extract_sources")
        parser.print_help()
        return

    # Handle output directory setup
    if args.output_dir:
        if not os.path.exists(args.output_dir):
            os.makedirs(args.output_dir)
        else:
            if os.listdir(args.output_dir):
                logging.info("Output directory is not empty...")

    proxy = args.proxy
    output_dir = args.output_dir

    if proxy:
        logging.info(f"Using proxy: {proxy}")

    # Handle local sourcemap file
    if args.map_file:
        try:
            logging.info(f"Loading sourcemap from local file: {args.map_file}")
            sourcemap_json = load_sourcemap_from_file(args.map_file)

            # Analyze the sourcemap
            analyze_sourcemap(sourcemap_json)

            if args.extract_sources:
                # Check and clean output directory if needed
                if not check_and_clean_output_directory(output_dir):
                    return

                # Extract the source files
                extract_source_files(sourcemap_json, output_dir)

        except Exception as e:
            logging.error(f"Error processing sourcemap file: {e}")
            return

    # Handle URL-based sourcemap discovery
    else:
        url = args.url.strip()
        if not url:
            logging.error("No URL provided")
            return

        logging.info(f"Checking {url} for script tags...")
        # Get script tags from head
        scripts = get_script_tags(url, proxy)

        if not scripts:
            logging.info("No script tags with src attributes from this domain found")
            return

        logging.info(f"Found {len(scripts)} script tag(s):")
        for i, script in enumerate(scripts, 1):
            logging.info(f"{script['src']}")

        logging.info("Checking for source map files...")

        # Check for source maps using async
        script_urls = [script["src"] for script in scripts]
        sourcemap_results = asyncio.run(check_for_sourcemaps(script_urls, proxy))

        # Summary
        logging.info("Summary:")
        total_sourcemaps = sum(
            len(sourcemaps) for sourcemaps in sourcemap_results.values()
        )
        logging.info(f"Scripts checked: {len(scripts)}")
        logging.info(f"Source maps found: {total_sourcemaps}")

        sourcemap_json_array = []
        if total_sourcemaps > 0:
            logging.info("All source maps found:")
            for script_url, sourcemaps in sourcemap_results.items():
                if sourcemaps:
                    for sourcemap in sourcemaps:
                        if args.json:
                            sourcemap_json_array.append(sourcemap)
                        else:
                            print(f"{sourcemap['url']}")
                        if args.extract_sources:
                            try:
                                url_parsed = urlparse(script_url)
                                hostname = url_parsed.hostname

                                output_dir = f"{args.output_dir}/{hostname}/{url_parsed.path.split('/')[-1]}"
                                # Replace .. with . in output_dir
                                output_dir = output_dir.replace("..", ".")
                                # Check and clean output directory if needed
                                if not check_and_clean_output_directory(output_dir):
                                    continue
                                # Check if output_dir is a valid directory with netloc inside it , otherwise create it
                                if not os.path.exists(output_dir):
                                    os.makedirs(output_dir)

                                logging.info(
                                    f"Downloading sourcemap from: {sourcemap['url']}"
                                )
                                sourcemap_json = download_sourcemap(sourcemap["url"])

                                # Analyze the sourcemap
                                analyze_sourcemap(sourcemap_json)

                                # Extract the source files
                                extract_source_files(sourcemap_json, output_dir)

                            except requests.exceptions.RequestException as e:
                                logging.info(f"Error downloading sourcemap: {e}")
                            except json.JSONDecodeError as e:
                                logging.info(f"Error parsing sourcemap JSON: {e}")
                            except Exception as e:
                                logging.error(f"Unexpected error: {e}")
        if args.json and len(sourcemap_json_array) > 0:
            print(json.dumps(sourcemap_json_array, indent=4))


if __name__ == "__main__":
    main()
