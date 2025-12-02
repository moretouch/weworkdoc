#!/usr/bin/env python3
"""
WeWork API Documentation Crawler

This script crawls the WeWork API documentation from 
https://developer.work.weixin.qq.com/document/ and converts it to MDX format
for easier consumption by LLM agents and users.

The crawler:
1. Extracts the document tree structure from the main page
2. Fetches individual document content using the provided API
3. Converts HTML content to MDX format
4. Organizes files according to the document tree structure
"""

import random
import os
import re
import json
import time
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, quote
import requests


class WeWorkDocCrawler:
    def __init__(self, base_url: str = "https://developer.work.weixin.qq.com",
                 output_dir: str = "docs"):
        self.base_url = base_url
        self.output_dir = Path(output_dir)
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })

        # Setup logging
        logging.basicConfig(level=logging.INFO,
                            format='%(asctime)s - %(levelname)s - %(message)s')
        self.logger = logging.getLogger(__name__)

    def fetch_page(self, url: str) -> Optional[str]:
        """Fetch a web page and return its content."""
        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            return response.text
        except Exception as e:
            self.logger.error(f"Error fetching {url}: {e}")
            return None

    def extract_categories_from_page(self, url: str) -> Optional[List[Dict]]:
        """Extract categories from the main document page."""
        self.logger.info(f"Extracting categories from {url}")

        content = self.fetch_page(url)
        if not content:
            return None

        # Find window.settings object
        pattern = r'window\.settings\s*=\s*({.*?});'
        match = re.search(pattern, content, re.DOTALL)

        if not match:
            self.logger.error("Could not find window.settings in the page")
            return None

        try:
            settings_str = match.group(1)
            # Parse JSON (might need to handle some JS-specific syntax)
            settings_str = settings_str[0:settings_str.index('</script>')]
            settings = json.loads(settings_str)
            return settings.get('categories', [])
        except json.JSONDecodeError as e:
            self.logger.error(f"Error parsing window.settings JSON: {e}")
            return None

    def fetch_document_content(self, doc_id: str) -> Optional[Dict]:
        """Fetch document content using the provided API."""
        api_url = "https://developer.work.weixin.qq.com/docFetch/fetchCnt"

        headers = {
            "accept": "application/json, text/plain, */*",
            "accept-language": "zh-CN,zh;q=0.9",
            "content-type": "application/x-www-form-urlencoded",
            "priority": "u=1, i",
            "referer": "https://developer.work.weixin.qq.com/document/path/" + doc_id,
        }

        data = f"doc_id={doc_id}"
        params = {
            "lang": "zh_CN",
            "ajax": "1",
            "f": "json",
            "random": str(random.randint(10 ** 5 + 1, 10 ** 6)),  # This might need to be randomized
        }

        try:
            response = self.session.post(api_url, headers=headers, data=data,
                                         params=params, timeout=30)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            self.logger.error(f"Error fetching document {doc_id}: {e}")
            raise e
            # return None

    def build_category_tree(self, categories: List[Dict]) -> Dict:
        """Build a hierarchical tree from flat categories list."""
        # Create a mapping of id to category
        cat_map = {cat['id']: cat for cat in categories}

        # Build the tree structure
        tree = {}

        for cat in categories:
            parent_id = cat.get('parent_id', 0)

            if parent_id == 0:
                # Root category
                tree[cat['category_id']] = {
                    'category': cat,
                    'children': {}
                }
            else:
                # Find parent and add as child
                self._add_to_tree(tree, parent_id, cat)

        return tree

    def _add_to_tree(self, tree: Dict, parent_id: int, category: Dict):
        """Recursively add category to tree."""
        for node_id, node in tree.items():
            if node_id == parent_id:
                node['children'][category['category_id']] = {
                    'category': category,
                    'children': {}
                }
                return
            else:
                self._add_to_tree(node['children'], parent_id, category)

    def generate_file_path(self, category_path: List[str], title: str) -> Path:
        """Generate file path based on category hierarchy."""
        # Clean up path components
        clean_path = []
        for component in category_path:
            # Remove special characters and normalize
            clean_component = re.sub(r'[<>:"/\\|?*]', '', component)
            clean_component = clean_component.strip()
            clean_path.append(clean_component)

        # Clean up filename
        clean_title = re.sub(r'[<>:"/\\|?*]', '', title)
        clean_title = clean_title.strip()

        # Build full path
        full_path = self.output_dir / Path(*clean_path) / f"{clean_title}.mdx"
        return full_path

    def md_to_mdx(self, markdown_content: str, title: str, category_id: int, update_time: int = 0) -> str:
        """Convert markdown content to MDX format by adding frontmatter."""
        if not markdown_content:
            return f"# {title}\n\n*No content available*\n"

        # Add MDX frontmatter to existing markdown
        frontmatter = f"""---
title: "{title}"
generated_at: "{time.strftime('%Y-%m-%d %H:%M:%S')}"
update_time: {update_time}
source: "https://developer.work.weixin.qq.com/document/path/{category_id}"
---

"""

        return frontmatter + markdown_content

    def extract_update_time_from_mdx(self, file_path: Path) -> Optional[int]:
        """Extract update_time from existing MDX file's frontmatter."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Match frontmatter and extract update_time
            pattern = r'^---\s*\n.*?update_time:\s*(\d+).*?\n---'
            match = re.search(pattern, content, re.DOTALL | re.MULTILINE)
            
            if match:
                return int(match.group(1))
            return None
        except Exception as e:
            self.logger.error(f"Error reading update_time from {file_path}: {e}")
            return None

    def save_document(self, file_path: Path, content: str):
        """Save document content to file."""
        try:
            # Create directory if it doesn't exist
            file_path.parent.mkdir(parents=True, exist_ok=True)

            # Write content
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)

            self.logger.info(f"Saved: {file_path}")
        except Exception as e:
            self.logger.error(f"Error saving {file_path}: {e}")

    def crawl_tree(self, tree: Dict, path: List[str] = None):
        """Recursively crawl the document tree."""
        if path is None:
            path = []

        for node_id, node in tree.items():
            category = node['category']
            current_path = path + [category['title']]

            # Check if this is a leaf node (has doc_id)
            if category.get('doc_id', 0) > 0:
                # Generate file path
                file_path = self.generate_file_path(current_path[:-1], category['title'])
                doc_id = category['doc_id']
                update_time = category.get('time', 0)  # 文档的更新时间
                
                # Check if file needs to be updated
                needs_update = True
                if os.path.exists(file_path):
                    existing_update_time = self.extract_update_time_from_mdx(file_path)
                    if existing_update_time is not None and existing_update_time >= update_time:
                        self.logger.info(f"Skipping {category['title']} (already up-to-date)")
                        needs_update = False
                    else:
                        self.logger.info(f"Updating {category['title']} (local: {existing_update_time}, remote: {update_time})")
                
                if needs_update:
                    self.logger.info(f"Processing document: {category['title']} (ID: {doc_id})")

                    # Fetch document content
                    doc_data = self.fetch_document_content(str(doc_id))

                    if doc_data and doc_data.get('data'):
                        data = doc_data['data']
                        title = data.get('title', category['title'])

                        # Use markdown content directly from API
                        content_md = data.get('content_md', '')

                        # Convert to MDX (just add frontmatter to existing markdown)
                        mdx_content = self.md_to_mdx(content_md, title, category['category_id'], update_time)
                        # Save document
                        self.save_document(file_path, mdx_content)
                    else:
                        raise Exception("No data found in the response")
                    # Add delay to be respectful
                    time.sleep(1)

            # Recursively process children
            if node['children']:
                self.crawl_tree(node['children'], current_path)

    def run(self):
        """Main crawler execution."""
        self.logger.info("Starting WeWork documentation crawler...")

        # Create output directory
        self.output_dir.mkdir(exist_ok=True)

        # Extract categories from main page
        main_url = f"{self.base_url}/document/path/90195"
        categories = self.extract_categories_from_page(main_url)
        # 更新时间 categories[0]['time']
        if not categories:
            self.logger.error("Failed to extract categories")
            return

        self.logger.info(f"Found {len(categories)} categories")

        # Build category tree
        tree = self.build_category_tree(categories)

        # Start crawling
        self.crawl_tree(tree)

        self.logger.info("Crawling completed!")


if __name__ == "__main__":
    crawler = WeWorkDocCrawler()
    crawler.run()
    
