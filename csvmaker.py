import json
import ijson
import csv
import os
import datetime
import ijson
from colorama import init, Fore, Style
import tempfile
from decimal import Decimal
import re

# Initialize colorama
init(autoreset=True)

def process_first_name(first_name: str) -> str:
    """Clean first name: strip non-letters/spaces, trim, title-case each word.
    Matches AppScript cleanFirstNames() logic exactly — always returns a clean name."""
    if not first_name:
        return ''
    name = re.sub(r'[^a-zA-Z\s]', '', str(first_name))
    name = name.strip()
    name = re.sub(r'\b(\w)', lambda m: m.group(1).upper(), name)
    return name

def convert_decimals_to_float(data):
    """Recursively convert Decimal to float."""
    if isinstance(data, Decimal):
        return float(data)
    if isinstance(data, dict):
        return {key: convert_decimals_to_float(value) for key, value in data.items()}
    if isinstance(data, list):
        return [convert_decimals_to_float(item) for item in data]
    return data

def format_location_details(all_locations: list) -> tuple:
    """
    Format location data into separate columns for CSV.
    Returns: (location_names, coordinates, post_links)
    """
    if not all_locations:
        return ('', '', '')
    
    # Limit to top 5 unique locations
    locations = all_locations[:5]
    
    location_names = []
    coordinates = []
    post_links = []
    
    for loc in locations:
        if loc.get('name'):
            location_names.append(loc['name'])
            
            lat = loc.get('lat')
            lng = loc.get('lng')
            if lat and lng:
                coordinates.append(f"{lat},{lng}")
            else:
                coordinates.append('N/A')
            
            if loc.get('post_link'):
                post_links.append(loc['post_link'])
            else:
                post_links.append('N/A')
    
    # Join with pipe separator
    location_names_str = ' | '.join(location_names)
    coordinates_str = ' | '.join(coordinates)
    post_links_str = ' | '.join(post_links)
    
    return (location_names_str, coordinates_str, post_links_str)

def create_csv_from_analyzed_json_efficiently(analyzed_json_path: str, output_csv_path: str):
    """
    Convert analyzed.json to CSV with enhanced location data.
    Memory-efficient streaming with ijson.
    """
    print(f"{Fore.CYAN}Starting enhanced CSV conversion with location data...{Style.RESET_ALL}")

    try:
        # Step 1: Sort by engagement rate
        print(f"{Fore.CYAN}Sorting creators by engagement rate...{Style.RESET_ALL}")
        creators_to_sort = []
        with open(analyzed_json_path, 'rb') as json_file:
            creators = ijson.items(json_file, 'creators.item')
            for creator in creators:
                engagement_rate = creator.get('average_engagement_rate', 0)
                creators_to_sort.append({'engagement_rate': engagement_rate, 'creator_data': creator})

        creators_to_sort.sort(key=lambda x: x['engagement_rate'], reverse=True)

        # Step 2: Write to temp file
        temp_file_path = None
        with tempfile.NamedTemporaryFile(mode='w+', delete=False, newline='', encoding='utf-8') as temp_file:
            temp_file_path = temp_file.name
            creators_data_for_dump = [convert_decimals_to_float(item['creator_data']) for item in creators_to_sort]
            json.dump(creators_data_for_dump, temp_file)
        
        # Step 3: Write CSV
        print(f"{Fore.CYAN}Writing data to CSV...{Style.RESET_ALL}")

        headers = [
            "email", "primary_social_link", "username", "first_name", "last_name", "creator_type",
            "address_city", "address_state", "address_country", "address_zip",
            "latitude", "longitude",
            "posts_with_location", "total_posts_scraped",
            "collaboration_status", "top_collaboration", "top_collaboration_brand_logo",
            "niche_primary", "niche_secondary", "follower_count", "creator_size",
            "age_group", "age", "gender", "phone_number", "profile_picture",
            "tiktok_link", "youtube_link", "x_link", "linktree_link", "other_social_media",
            "business_category",
            "bio_data", "last_updated", "source",
            "total_collaborations_in_recent_25_posts", "ugc_examples",
            "latest_post_link", "latest_post_date",
            "scraped_date", "analyzed_date"
        ]

        for j in range(1, 26):
            headers += [
                f"post{j}_link", f"post{j}_likes_count", f"post{j}_comments_count",
                f"post{j}_views_count", f"post{j}_reposts_count", f"post{j}_captions",
                f"post{j}_hashtags", f"post{j}_mention", f"post{j}_address",
                f"post{j}_city", f"post{j}_state", f"post{j}_country",
                f"post{j}_latitude", f"post{j}_longitude",
            ]

        total_users = 0
        with open(output_csv_path, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.writer(csvfile, quoting=csv.QUOTE_MINIMAL)
            writer.writerow(headers)
            
            with open(temp_file_path, 'r', encoding='utf-8') as temp_json_file:
                creators_data = json.load(temp_json_file)
                for creator in creators_data:
                    total_users += 1

                    username = creator.get('username', '')
                    first_name_raw = creator.get('first_name', '')
                    processed_first_name = process_first_name(first_name_raw)
                    # last_name: split full_name, take everything after first word, clean same way
                    full_name_raw = creator.get('full_name', '') or ''
                    full_name_parts = full_name_raw.strip().split(None, 1)
                    last_name_raw = full_name_parts[1] if len(full_name_parts) > 1 else ''
                    processed_last_name = re.sub(r'\b(\w)', lambda m: m.group(1).upper(), re.sub(r'[^a-zA-Z\s]', '', last_name_raw).strip())
                    email = creator.get('email', '')

                    # Location — from bio (same source as ai_analyzed)
                    address_city    = creator.get('address_city', '')
                    address_state   = creator.get('address_state', '')
                    address_country = creator.get('address_country', '')
                    address_zip     = creator.get('address_zip', '')
                    latitude        = creator.get('latitude', '')
                    longitude       = creator.get('longitude', '')

                    posts_with_location = creator.get('posts_with_location', 0)
                    total_posts_scraped = creator.get('total_posts_scraped', 0)

                    collaboration_status = creator.get('collaboration_status', '')

                    # top_collaboration — all sources (paid_partnership, tag, owner, coauthor)
                    # Brand logo only for confirmed paid_partnership AND brand != creator's own username
                    # (owner/coauthor entries are regular users whose profile pic would show otherwise)
                    top_collaboration_names = []
                    top_collaboration_brand_logo_list = []
                    creator_username_lower = username.lower()
                    for collab in creator.get('top_collaboration', []):
                        brand_name = (collab.get('name') or '').strip()
                        if not brand_name:
                            continue
                        top_collaboration_names.append(brand_name)
                        # Only generate logo for paid_partnership and only if it's not the creator themselves
                        if (collab.get('source') == 'paid_partnership'
                                and brand_name.lower() != creator_username_lower):
                            logo_url = f"https://assets.veelapp.com/{brand_name}.jpg"
                            top_collaboration_brand_logo_list.append(f"{brand_name};{logo_url}")
                    top_collaboration_str = " | ".join(top_collaboration_names)
                    top_collaboration_brand_logo = " | ".join(top_collaboration_brand_logo_list)

                    # niche_primary — flat field added in updated analyzed.json, fallback to nested
                    niche_primary = creator.get('niche_primary') or creator.get('niche_data', {}).get('overall_niche', '')
                    niche_secondary = ''
                    creator_type    = creator.get('creator_type', '')
                    follower_count  = creator.get('follower_count', 0)
                    creator_size    = creator.get('creator_size', '')
                    age_group = creator.get('age_group','')
                    age       = ''
                    gender        = creator.get('gender', '')
                    phone_number  = creator.get('phone_number', '')
                    profile_picture = creator.get('profile_picture', '')

                    social_links  = creator.get('social_links', {}) or {}
                    tiktok_link   = social_links.get('tiktok', '') or ''
                    youtube_link  = social_links.get('youtube', '') or ''
                    x_link        = social_links.get('x', '') or ''
                    linktree_link = social_links.get('linktree', '') or ''
                    other_social_media = " | ".join(link for link in [tiktok_link, youtube_link, x_link, linktree_link] if link)

                    primary_social_link = f"https://www.instagram.com/{username}" if username else ''
                    business_category   = creator.get('business_category', '')
                    bio_data            = (creator.get('biography', '') or '').replace('\n', ' ').replace(',', ' ')
                    last_updated        = creator.get('analyzed_date', '')
                    source              = creator.get('source', '')
                    total_collaborations = creator.get('total_collaborations', 0)
                    ugc_examples        = creator.get('ugc_examples', '')
                    latest_post_link    = creator.get('latest_post_link', '')
                    latest_post_date    = creator.get('latest_post_date', '')
                    scraped_date        = creator.get('scraped_date', '')
                    analyzed_date       = creator.get('analyzed_date', '')

                    row = [
                        email, primary_social_link, username, processed_first_name, processed_last_name, creator_type,
                        address_city, address_state, address_country, address_zip,
                        latitude, longitude,
                        posts_with_location, total_posts_scraped,
                        collaboration_status, top_collaboration_str, top_collaboration_brand_logo,
                        niche_primary, niche_secondary, follower_count, creator_size,
                        age_group, age, gender, phone_number, profile_picture,
                        tiktok_link, youtube_link, x_link, linktree_link, other_social_media,
                        business_category,
                        bio_data, last_updated, source,
                        total_collaborations, ugc_examples,
                        latest_post_link, latest_post_date,
                        scraped_date, analyzed_date
                    ]

                    # 25 posts from new posts[] array (same structure as ai_analyzed)
                    posts = creator.get('posts', [])
                    for j in range(25):
                        if j < len(posts):
                            p = posts[j]
                            loc = p.get('post_location') or {}
                            row += [
                                p.get('post_link', ''),
                                p.get('likes_count', ''),
                                p.get('comments_count', ''),
                                p.get('views_count', ''),
                                p.get('reposts_count', ''),
                                (p.get('caption', '') or '').replace('\n', ' ').replace(',', ' '),
                                ' | '.join(p.get('hashtags', [])),
                                ' | '.join(p.get('mentions', [])),
                                loc.get('address', ''),
                                loc.get('city', ''),
                                loc.get('state', ''),
                                loc.get('country', ''),
                                loc.get('latitude', ''),
                                loc.get('longitude', ''),
                            ]
                        else:
                            row += [''] * 14

                    cleaned_row = [str(item).replace(',', '') if isinstance(item, str) else item for item in row]
                    writer.writerow(cleaned_row)
        
        os.remove(temp_file_path)

        return True, total_users

    except Exception as e:
        print(f"{Fore.RED}Error processing JSON: {str(e)}{Style.RESET_ALL}")
        if temp_file_path and os.path.exists(temp_file_path):
            os.remove(temp_file_path)
        return False, 0

def main():
    """Main function with location data support."""
    print(f"{Fore.CYAN}{'='*70}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}Enhanced JSON to CSV Converter with Location Intelligence{Style.RESET_ALL}")
    print(f"{Fore.CYAN}{'='*70}{Style.RESET_ALL}\n")
    
    while True:
        raw = input("📁  Enter the JSON file name (e.g. myproject.json): ").strip()
        if raw:
            analyzed_json_file = raw if raw.endswith('.json') else raw + '.json'
            break
        print("    ⚠  File name cannot be empty.")

    today_date = datetime.datetime.now().strftime('%Y%m%d')
    output_csv_file = f"output{today_date}.csv"

    if not os.path.exists(analyzed_json_file):
        print(f"{Fore.RED}{analyzed_json_file} not found!{Style.RESET_ALL}")
        print(f"{Fore.YELLOW}Run the analyzer script first{Style.RESET_ALL}")
        return

    print(f"{Fore.GREEN}✓ Found {analyzed_json_file}{Style.RESET_ALL}")
    print(f"{Fore.GREEN}✓ Output: {output_csv_file}{Style.RESET_ALL}\n")
    
    try:
        with open(analyzed_json_file, 'rb') as json_file:
            parser = ijson.parse(json_file)
            analyzed_data_info = {}
            for prefix, event, value in parser:
                if prefix == 'analysis_date' and event == 'string':
                    analyzed_data_info['analysis_date'] = value
                if prefix == 'total_creators_analyzed' and event == 'number':
                    analyzed_data_info['total_creators_analyzed'] = int(value)
                if prefix == 'creators_with_location' and event == 'number':
                    analyzed_data_info['creators_with_location'] = int(value)
                if prefix == 'location_coverage_percentage' and event == 'number':
                    analyzed_data_info['location_coverage_percentage'] = float(value)
                if prefix == 'creators' and event == 'start_array':
                    break
            
            analysis_date = analyzed_data_info.get('analysis_date', 'Unknown')
            total_creators = analyzed_data_info.get('total_creators_analyzed', 0)
            creators_with_location = analyzed_data_info.get('creators_with_location', 0)
            location_percentage = analyzed_data_info.get('location_coverage_percentage', 0)
            
            print(f"{Fore.CYAN}Analysis Information:{Style.RESET_ALL}")
            print(f"  Date: {analysis_date}")
            print(f"  Total Creators: {total_creators}")
            print(f"  With Location: {creators_with_location} ({location_percentage}%)")
            print()
    except Exception as e:
        print(f"{Fore.RED}Error reading JSON metadata: {str(e)}{Style.RESET_ALL}")
        return
    
    success, total_users = create_csv_from_analyzed_json_efficiently(analyzed_json_file, output_csv_file)
    
    if success:
        print(f"\n{Fore.GREEN}{'='*70}{Style.RESET_ALL}")
        print(f"{Fore.GREEN}✓ CSV created successfully: {output_csv_file}{Style.RESET_ALL}")
        print(f"{Fore.GREEN}✓ Total users converted: {total_users}{Style.RESET_ALL}")
        print(f"{Fore.GREEN}{'='*70}{Style.RESET_ALL}\n")
        
        print(f"{Fore.YELLOW}New Location Columns Added:{Style.RESET_ALL}")
        print(f"  • primary_location_name")
        print(f"  • latitude, longitude")
        print(f"  • all_location_names (pipe-separated)")
        print(f"  • all_location_coordinates (pipe-separated)")
        print(f"  • all_location_post_links (pipe-separated)")
        print(f"  • posts_with_location / total_posts_scraped")
        print(f"  • address_city, address_state, address_country")
        print()
        
    else:
        print(f"{Fore.RED}✗ Conversion failed!{Style.RESET_ALL}")

if __name__ == "__main__":
    main()