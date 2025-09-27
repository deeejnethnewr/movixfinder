import os
import json
import requests
import logging
import base64
import re
import asyncio
import time
import threading
from queue import Queue
from datetime import datetime
from imdb import IMDb
import telebot
from telebot import types
from openai import OpenAI

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class MovieBot:
    def __init__(self):
        # Get API keys from environment variables
        self.telegram_token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.omdb_api_key = os.getenv('OMDB_API_KEY')
        # Set OpenAI API key from environment variable
        self.openai_api_key = os.getenv('OPENAI_API_KEY')

        # Get Telegram client credentials
        self.api_id = os.getenv('API_ID')
        self.api_hash = os.getenv('API_HASH')

        if not self.telegram_token:
            raise ValueError("TELEGRAM_BOT_TOKEN environment variable is required")

        # Initialize Telegram bot
        self.bot = telebot.TeleBot(self.telegram_token)

        # Initialize IMDb object (fallback)
        self.ia = IMDb()

        # Initialize OpenAI client for image recognition
        if self.openai_api_key:
            self.openai_client = OpenAI(api_key=self.openai_api_key)
        else:
            self.openai_client = None
            logger.warning("OpenAI API key not provided - image recognition will be disabled")

        # Owner IDs - these users have special privileges
        self.owner_ids = [7302708535, 7262768284]

        # Required membership channels/groups
        self.required_channel = "@yxuplordermainchanal"  # Main channel
        self.required_group = "@yxofficial01"  # Main group

        # Database file path
        self.db_path = "mydata/manual_movie_database.json"
        self.ensure_database_exists()

        # Initialize waiting system
        self.request_queue = Queue()
        self.processing_requests = False
        self.setup_waiting_system()

        self.setup_handlers()
        self.setup_menu()

        # Start automatic admin management
        self.start_admin_management_task()

    def setup_menu(self):
        """Set up persistent menu with Database button"""
        try:
            menu_button = types.BotCommand("database", "Browse movie database")
            self.bot.set_my_commands([menu_button])
        except Exception as e:
            logger.error(f"Error setting up menu: {e}")

    def setup_waiting_system(self):
        """Set up the request waiting system"""
        def process_queue():
            while True:
                if not self.request_queue.empty():
                    self.processing_requests = True
                    request_data = self.request_queue.get()
                    try:
                        # Process the request
                        request_data['handler'](request_data['message'])
                    except Exception as e:
                        logger.error(f"Error processing queued request: {e}")
                    finally:
                        self.request_queue.task_done()
                        # Small delay between processing requests
                        time.sleep(0.5)
                else:
                    self.processing_requests = False
                    time.sleep(0.1)

        # Start the queue processor in a separate thread
        queue_thread = threading.Thread(target=process_queue, daemon=True)
        queue_thread.start()

    def add_to_queue(self, message, handler):
        """Add a request to the waiting queue with 2-second delay"""
        def delayed_add():
            time.sleep(2)
            self.request_queue.put({
                'message': message,
                'handler': handler,
                'timestamp': time.time()
            })

        delay_thread = threading.Thread(target=delayed_add, daemon=True)
        delay_thread.start()

    def should_protect_content(self, chat_id):
        """Check if content should be protected based on owner IDs"""
        return chat_id not in self.owner_ids

    def send_support_button(self, chat_id):
        """Send support button for video playback issues"""
        markup = types.InlineKeyboardMarkup()
        support_btn = types.InlineKeyboardButton("🛠️ Contact Support", url="https://t.me/p2bpas")
        markup.add(support_btn)

        support_msg = "🎬 **Video Support**\n\nIf videos are not playing properly, please contact our support team for assistance."
        self.bot.send_message(chat_id, support_msg, reply_markup=markup, parse_mode='Markdown', protect_content=self.should_protect_content(chat_id))

    def send_copyright_messages(self, chat_id):
        """Send copyright and source information messages"""
        # Copyright disclaimer
        copyright_msg = """📋 **Copyright Notice**

⚖️ All videos shared through this bot are for educational and entertainment purposes only.

🌐 **Content**: All videos have no copyright issues and are shared with proper permissions.

📝 We respect intellectual property rights and will remove any content upon valid copyright claims."""

        self.bot.send_message(chat_id, copyright_msg, parse_mode='Markdown', protect_content=self.should_protect_content(chat_id))

    def send_welcome_message(self, chat_id):
        """Send welcome message for file upload"""
        welcome_msg = """🎬 **Welcome to Movie Upload**

Thank you for contributing to our movie database! 

📁 Your file is being processed...
⏳ Please wait while we add your movie to the database."""

        return self.bot.send_message(chat_id, welcome_msg, parse_mode='Markdown', protect_content=self.should_protect_content(chat_id))

    def send_thank_you_message(self, chat_id):
        """Send thank you message after upload completion"""
        thank_you_msg = """✅ **Upload Complete**

🙏 Thank you for your contribution to our movie database!

🎬 Your movie has been successfully added and is now available for all users.

💝 We appreciate your support in building our community collection!"""

        self.bot.send_message(chat_id, thank_you_msg, parse_mode='Markdown', protect_content=self.should_protect_content(chat_id))

    def ensure_database_exists(self):
        """Ensure the database directory and file exist"""
        os.makedirs("mydata", exist_ok=True)
        if not os.path.exists(self.db_path):
            with open(self.db_path, 'w') as f:
                json.dump({}, f)

    def load_database(self):
        """Load the database from JSON file"""
        try:
            with open(self.db_path, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if not content:
                    logger.warning("Database file is empty, initializing new database")
                    return {}
                return json.loads(content)
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error in database: {e}")
            # Backup corrupted file and create new one
            import shutil
            backup_path = f"{self.db_path}.backup"
            shutil.copy2(self.db_path, backup_path)
            logger.info(f"Corrupted database backed up to {backup_path}")
            return {}
        except Exception as e:
            logger.error(f"Error loading database: {e}")
            return {}

    def save_database(self, data):
        """Save the database to JSON file"""
        try:
            with open(self.db_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error saving database: {e}")

    def check_user_membership(self, user_id):
        """Check if user is a member of required channel and group"""
        try:
            # Check channel membership
            channel_member = self.bot.get_chat_member(self.required_channel, user_id)
            channel_status = channel_member.status in ['member', 'administrator', 'creator']

            # Check group membership
            group_member = self.bot.get_chat_member(self.required_group, user_id)
            group_status = group_member.status in ['member', 'administrator', 'creator']

            return channel_status and group_status
        except Exception as e:
            logger.error(f"Error checking membership for user {user_id}: {e}")
            return False

    def send_membership_required_message(self, message):
        """Send message requiring membership to channel and group"""
        markup = types.InlineKeyboardMarkup()

        # Create buttons for joining
        channel_btn = types.InlineKeyboardButton("📺 Join Main Channel", url="https://t.me/yxuplordermainchanal")
        group_btn = types.InlineKeyboardButton("👥 Join Main Group", url="https://t.me/yxofficial01")
        check_btn = types.InlineKeyboardButton("✅ I Joined Both", callback_data="check_membership")

        markup.row(channel_btn)
        markup.row(group_btn)
        markup.row(check_btn)

        membership_text = """
🚫 **Access Restricted**

To use this bot, you must join our main channel and group:

📺 **Main Channel**: @yxuplordermainchanal
👥 **Main Group**: @yxofficial01

Please join both and click "I Joined Both" to continue.
        """

        self.bot.send_message(message.chat.id, membership_text, reply_markup=markup, parse_mode='Markdown', protect_content=self.should_protect_content(message.chat.id))

    def handle_group_interaction(self, message):
        """Handle when user interacts with bot in a group"""
        if message.chat.type in ['group', 'supergroup']:
            # Cache this group for admin management
            self.save_group_to_cache(message.chat.id)

            # Check if user has required membership
            if not self.check_user_membership(message.from_user.id):
                # Send private message to user
                try:
                    markup = types.InlineKeyboardMarkup()
                    start_btn = types.InlineKeyboardButton("🤖 Start Bot", url=f"https://t.me/{self.bot.get_me().username}?start=group_access")
                    markup.add(start_btn)

                    group_msg = f"👋 Hello {message.from_user.first_name}!\n\nTo use this bot in groups, please start the bot privately first."

                    self.bot.send_message(message.chat.id, group_msg, reply_markup=markup, reply_to_message_id=message.message_id, protect_content=self.should_protect_content(message.chat.id))
                except Exception as e:
                    logger.error(f"Error sending group interaction message: {e}")
                return False
        return True

    def extract_imdb_id_from_url(self, imdb_url):
        """Extract IMDb ID from URL"""
        # Match both tt##### and ##### formats, handle various URL endings
        match = re.search(r'/title/(tt)?(\d+)(?:/|\?|$)', imdb_url)
        if match:
            # If tt prefix exists, return it with the number, otherwise add tt prefix
            tt_prefix = match.group(1) if match.group(1) else 'tt'
            number = match.group(2)
            return f"{tt_prefix}{number}"
        return None

    def parse_text_file_content(self, content):
        """Parse text file content to extract IMDb URL, video links with group names, and filter words"""
        lines = [line.strip() for line in content.split('\n') if line.strip()]

        imdb_url = None
        video_links = []
        filter_words = []

        for line in lines:
            if 'imdb.com/title/' in line:
                imdb_url = line
            elif 't.me/' in line:
                # Extract group name from URL for storage
                group_name = self.extract_group_name_from_url(line)
                video_links.append({
                    'url': line,
                    'group_name': group_name
                })
            elif line.startswith('#'):
                # Extract filter words (remove # symbol and split by commas)
                filter_line = line[1:].strip()  # Remove # symbol
                # Split by commas and clean up each filter word
                words = [word.strip().lower() for word in filter_line.split(',') if word.strip()]
                filter_words.extend(words)

        return imdb_url, video_links, filter_words

    def extract_group_name_from_url(self, url):
        """Extract group name from Telegram URL"""
        # For URLs like https://t.me/c/3185863680/60, extract the chat ID
        match = re.search(r'/c/(\d+)/', url)
        if match:
            chat_id = match.group(1)
            return f"Group_{chat_id}"  # We'll store a generic name since we can't get actual group name
        return "Unknown_Group"

    def get_movie_info_from_imdb(self, imdb_id):
        """Get movie information from IMDb ID"""
        try:
            movie = self.ia.get_movie(imdb_id.replace('tt', ''))

            # Safely extract information
            title = movie.get('title', 'Unknown')
            year = movie.get('year', 'N/A')
            directors = movie.get('directors', [])
            director_str = ', '.join([str(d) for d in directors]) if directors else 'N/A'
            genres = movie.get('genres', [])
            genre_str = ', '.join(genres) if genres else 'N/A'
            plot_data = movie.get('plot outline') or movie.get('plot')
            if plot_data:
                plot = plot_data[0] if isinstance(plot_data, list) else plot_data
            else:
                plot = 'N/A'
            rating = movie.get('rating', 'N/A')

            return {
                'title': title,
                'year': year,
                'director': director_str,
                'genre': genre_str,
                'plot': plot,
                'rating': rating,
                'imdb_id': imdb_id
            }
        except Exception as e:
            logger.error(f"Error getting movie info from IMDb: {e}")
            return None

    def forward_video_by_url(self, chat_id, video_url):
        """Send video message with custom caption by extracting chat and message ID from URL"""
        try:
            # Extract chat ID and message ID from URL
            # Format: https://t.me/c/3185863680/110
            match = re.search(r'/c/(\d+)/(\d+)', video_url)
            if not match:
                logger.error(f"Could not parse video URL: {video_url}")
                return False

            source_chat_id = int(f"-100{match.group(1)}")  # Add -100 prefix and convert to int
            message_id = int(match.group(2))

            # Custom caption for every video
            custom_caption = """Thank you for choosing us! 🌟

...:::Yx_Uplorder_™:::..."""

            try:
                # Get the original message to extract video information
                original_message = self.bot.forward_message(chat_id, source_chat_id, message_id, protect_content=False)

                # Delete the forwarded message immediately
                self.bot.delete_message(chat_id, original_message.message_id)

                # Prepend custom message to original caption
                original_caption = original_message.caption or ""
                custom_prepend = """Happy watching! 📺

Upload by @yxuplordermainchanal with ...:::Yx_Uplorder_™:::...

"""
                
                # Combine custom prepend with original caption
                if original_caption:
                    combined_caption = custom_prepend + original_caption
                else:
                    combined_caption = custom_prepend.strip()  # Remove trailing newlines if no original caption

                # Create new markup with Group and Channel buttons
                new_markup = types.InlineKeyboardMarkup()
                
                # Add our new buttons
                group_btn = types.InlineKeyboardButton("👥 Group", url="https://t.me/yxofficial01")
                channel_btn = types.InlineKeyboardButton("📺 Channel", url="https://t.me/yxuplordermainchanal")
                new_markup.row(group_btn, channel_btn)

                # If original message had buttons, preserve them
                if original_message.reply_markup and original_message.reply_markup.keyboard:
                    # Add original buttons after our new buttons
                    for row in original_message.reply_markup.keyboard:
                        new_markup.keyboard.append(row)

                # Send the video with combined caption and enhanced buttons
                if original_message.video:
                    self.bot.send_video(
                        chat_id, 
                        original_message.video.file_id,
                        caption=combined_caption,
                        reply_markup=new_markup,
                        protect_content=self.should_protect_content(chat_id)
                    )
                elif original_message.document:
                    self.bot.send_document(
                        chat_id, 
                        original_message.document.file_id,
                        caption=combined_caption,
                        reply_markup=new_markup,
                        protect_content=self.should_protect_content(chat_id)
                    )
                elif original_message.animation:
                    self.bot.send_animation(
                        chat_id, 
                        original_message.animation.file_id,
                        caption=combined_caption,
                        reply_markup=new_markup,
                        protect_content=self.should_protect_content(chat_id)
                    )
                else:
                    # Fallback: just forward with protection
                    self.bot.forward_message(chat_id, source_chat_id, message_id, protect_content=self.should_protect_content(chat_id))

                return True

            except Exception as forward_error:
                logger.error(f"Error in custom forwarding, trying regular forward: {forward_error}")
                # Fallback to regular forwarding if custom method fails
                self.bot.forward_message(chat_id, source_chat_id, message_id, protect_content=self.should_protect_content(chat_id))
                return True

        except Exception as e:
            logger.error(f"Error forwarding video from {video_url}: {e}")
            return False

    def promote_user_to_admin(self, chat_id, user_id):
        """Promote a user to admin in the specified chat"""
        try:
            self.bot.promote_chat_member(
                chat_id,
                user_id,
                can_change_info=True,
                can_delete_messages=True,
                can_invite_users=True,
                can_restrict_members=True,
                can_pin_messages=True,
                can_promote_members=False  # Don't let them promote others
            )
            return True
        except Exception as e:
            logger.error(f"Error promoting user {user_id} in chat {chat_id}: {e}")
            return False

    def start_admin_management_task(self):
        """Start background task for automatic admin management"""
        def admin_management_loop():
            """Background loop to manage admin permissions every 10 seconds"""
            while True:
                try:
                    self.check_all_groups_admin_permissions()
                    time.sleep(10)  # Check every 10 seconds
                except Exception as e:
                    logger.error(f"Error in admin management loop: {e}")
                    time.sleep(10)  # Continue checking even if there's an error

        # Start the admin management in a separate thread
        admin_thread = threading.Thread(target=admin_management_loop, daemon=True)
        admin_thread.start()
        logger.info("Started automatic admin management task (checking every 10 seconds)")

    def get_all_managed_groups(self):
        """Get list of all groups where bot is admin (from database/cache)"""
        # Since Telegram API doesn't provide a direct way to get all groups,
        # we'll maintain a simple cache of groups the bot has interacted with
        try:
            cache_file = "mydata/group_cache.json"
            if os.path.exists(cache_file):
                with open(cache_file, 'r') as f:
                    content = f.read().strip()
                    if content:
                        return json.loads(content)
            return []
        except Exception as e:
            logger.error(f"Error loading group cache: {e}")
            return []

    def save_group_to_cache(self, chat_id):
        """Save a group to the cache if it's a group and bot is admin"""
        try:
            cache_file = "mydata/group_cache.json"
            os.makedirs("mydata", exist_ok=True)

            # Load existing cache
            cached_groups = self.get_all_managed_groups()

            # Add new group if not already cached
            if chat_id not in cached_groups:
                cached_groups.append(chat_id)

                # Save updated cache
                with open(cache_file, 'w') as f:
                    json.dump(cached_groups, f)

                logger.info(f"Added group {chat_id} to cache")
        except Exception as e:
            logger.error(f"Error saving group to cache: {e}")

    def check_all_groups_admin_permissions(self):
        """Check and manage admin permissions in all cached groups"""
        cached_groups = self.get_all_managed_groups()

        for chat_id in cached_groups:
            try:
                self.manage_group_admins_automatically(chat_id)
            except Exception as e:
                logger.error(f"Error checking group {chat_id}: {e}")
                # Remove from cache if group no longer accessible
                if "chat not found" in str(e).lower() or "forbidden" in str(e).lower():
                    self.remove_group_from_cache(chat_id)

    def remove_group_from_cache(self, chat_id):
        """Remove a group from cache"""
        try:
            cached_groups = self.get_all_managed_groups()
            if chat_id in cached_groups:
                cached_groups.remove(chat_id)
                cache_file = "mydata/group_cache.json"
                with open(cache_file, 'w') as f:
                    json.dump(cached_groups, f)
                logger.info(f"Removed group {chat_id} from cache")
        except Exception as e:
            logger.error(f"Error removing group from cache: {e}")

    def manage_group_admins_automatically(self, chat_id):
        """Automatically manage admin permissions in a specific group"""
        try:
            # Get chat administrators
            chat_members = self.bot.get_chat_administrators(chat_id)

            # Separate different types of members
            bot_is_admin = False
            bot_can_promote = False
            bot_can_invite = False
            owners_in_group = []
            current_admins = []
            chat_creator = None

            for member in chat_members:
                if member.user.id == self.bot.get_me().id:
                    bot_is_admin = True
                    bot_can_promote = member.can_promote_members
                    bot_can_invite = member.can_invite_users
                elif member.user.id in self.owner_ids:
                    owners_in_group.append(member.user.id)
                elif member.status == 'creator':
                    chat_creator = member
                elif member.status == 'administrator':
                    current_admins.append(member)

            if not bot_is_admin:
                logger.debug(f"Bot is not admin in group {chat_id}")
                return

            # Check if bot has required permissions
            if not bot_can_promote or not bot_can_invite:
                try:
                    missing_perms = []
                    if not bot_can_promote:
                        missing_perms.append("Add new admins")
                    if not bot_can_invite:
                        missing_perms.append("Invite users")

                    error_msg = f"""🚨 **Bot Permission Issue in Group**

**Group ID:** {chat_id}

The bot cannot function properly in this group because it lacks required admin permissions.

**Missing permissions:**
{chr(10).join(f"• {perm}" for perm in missing_perms)}

Please grant the bot these permissions so it can automatically manage owner access and admin permissions."""

                    # Send to all owners via private messages instead of group
                    for owner_id in self.owner_ids:
                        try:
                            self.bot.send_message(owner_id, error_msg, parse_mode='Markdown')
                        except Exception as dm_error:
                            logger.warning(f"Could not send permission warning to owner {owner_id}: {dm_error}")

                    logger.warning(f"Bot missing permissions in group {chat_id}: {missing_perms}")
                except Exception as e:
                    logger.error(f"Error sending permission warning to owners for group {chat_id}: {e}")
                return

            # Find missing owners
            missing_owners = [owner_id for owner_id in self.owner_ids if owner_id not in owners_in_group]

            # Try to add/promote missing owners
            owners_added = []
            owners_invited = []
            owners_failed_to_add = []

            for owner_id in missing_owners:
                try:
                    # Check if owner is in the group
                    member_info = self.bot.get_chat_member(chat_id, owner_id)

                    if member_info.status in ['member', 'restricted']:
                        # Owner is in group but not admin - promote them
                        success = self.promote_user_to_admin(chat_id, owner_id)
                        if success:
                            owners_added.append(owner_id)
                            logger.info(f"Promoted owner {owner_id} to admin in group {chat_id}")
                        else:
                            logger.warning(f"Failed to promote owner {owner_id} in group {chat_id}")
                    elif member_info.status == 'left':
                        # Owner left the group - try to invite them back
                        self.invite_owner_to_group(chat_id, owner_id, owners_invited, owners_failed_to_add)

                except Exception as e:
                    # Owner is not in the group - try to add them
                    logger.debug(f"Owner {owner_id} not in group {chat_id}: {e}")
                    self.invite_owner_to_group(chat_id, owner_id, owners_invited, owners_failed_to_add)

            # Send status messages to owner inboxes only, not in group
            if owners_failed_to_add:
                try:
                    failed_mentions = [f"[Owner](tg://user?id={owner_id})" for owner_id in owners_failed_to_add]

                    error_msg = f"""❌ **Failed to Add Owners to Group**

**Group ID:** {chat_id}

{' '.join(failed_mentions)}

The bot attempted to automatically invite owners to this group but encountered errors:

**Possible issues:**
• Owners have blocked the bot
• Privacy settings prevent invitations
• Bot lacks sufficient permissions
• Group settings restrict invitations

**Manual action required:**
Please manually add the missing owners to this group and grant them admin permissions.

**Failed to add:** {len(owners_failed_to_add)} owner(s)
**Successfully added:** {len(owners_added)} owner(s)"""

                    # Send to all owners via private messages
                    for owner_id in self.owner_ids:
                        try:
                            self.bot.send_message(owner_id, error_msg, parse_mode='Markdown')
                        except Exception as dm_error:
                            logger.warning(f"Could not send admin failure notification to owner {owner_id}: {dm_error}")

                    logger.error(f"Failed to add {len(owners_failed_to_add)} owners to group {chat_id}")
                except Exception as e:
                    logger.error(f"Error sending failure notification to owners for group {chat_id}: {e}")

            elif owners_invited:
                try:
                    invited_mentions = [f"[Owner](tg://user?id={owner_id})" for owner_id in owners_invited]

                    success_msg = f"""✅ **Owners Invited to Group Successfully**

**Group ID:** {chat_id}

{' '.join(invited_mentions)}

Invite links have been sent to the missing owners. They will be automatically granted admin permissions once they join the group.

**Invitations sent:** {len(owners_invited)} owner(s)
**Already admins:** {len(owners_in_group)} owner(s)"""

                    # Send to all owners via private messages
                    for owner_id in self.owner_ids:
                        try:
                            self.bot.send_message(owner_id, success_msg, parse_mode='Markdown')
                        except Exception as dm_error:
                            logger.warning(f"Could not send admin success notification to owner {owner_id}: {dm_error}")

                    logger.info(f"Successfully invited {len(owners_invited)} owners to group {chat_id}")
                except Exception as e:
                    logger.error(f"Error sending success notification to owners for group {chat_id}: {e}")

            # Log successful operations
            if owners_added:
                logger.info(f"Successfully promoted {len(owners_added)} owners to admin in group {chat_id}")

            # Ensure all existing admins have proper permissions
            all_current_admins = []
            for member in chat_members:
                if member.status == 'administrator' and member.user.id != self.bot.get_me().id:
                    all_current_admins.append(member)

            for admin in all_current_admins:
                # Skip creator as they have all permissions by default
                if admin.status == 'creator':
                    continue

                # Check if admin has all required permissions
                required_permissions = {
                    'can_change_info': True,
                    'can_delete_messages': True,
                    'can_invite_users': True,
                    'can_restrict_members': True,
                    'can_pin_messages': True,
                }

                needs_update = False
                for perm, required_value in required_permissions.items():
                    if getattr(admin, perm, False) != required_value:
                        needs_update = True
                        break

                if needs_update:
                    try:
                        success = self.promote_user_to_admin(chat_id, admin.user.id)
                        if success:
                            logger.info(f"Updated permissions for admin {admin.user.id} in group {chat_id}")
                    except Exception as e:
                        logger.error(f"Error updating admin permissions for {admin.user.id}: {e}")

        except Exception as e:
            logger.error(f"Error in automatic admin management for group {chat_id}: {e}")
            # If error suggests bot was removed or demoted, remove from cache
            if any(phrase in str(e).lower() for phrase in ["not found", "forbidden", "not enough rights"]):
                self.remove_group_from_cache(chat_id)

    def invite_owner_to_group(self, chat_id, owner_id, owners_invited, owners_failed_to_add):
        """Try to invite an owner to a group with multiple retry attempts"""
        max_retries = 3
        retry_count = 0

        while retry_count < max_retries:
            try:
                # First, ensure owner is not banned
                try:
                    self.bot.unban_chat_member(chat_id, owner_id, only_if_banned=True)
                except:
                    pass  # Ignore if user wasn't banned

                # Try to add owner directly first
                try:
                    self.bot.add_chat_member(chat_id, owner_id)
                    # If successful, promote to admin
                    time.sleep(1)  # Small delay before promotion
                    success = self.promote_user_to_admin(chat_id, owner_id)
                    if success:
                        owners_invited.append(owner_id)
                        logger.info(f"Successfully added and promoted owner {owner_id} in group {chat_id}")
                        return
                    else:
                        logger.warning(f"Added owner {owner_id} to group {chat_id} but failed to promote")
                        owners_invited.append(owner_id)
                        return

                except Exception as direct_add_error:
                    logger.debug(f"Direct add failed for owner {owner_id}: {direct_add_error}")

                    # If direct add fails, try creating invite link
                    try:
                        invite_link = self.bot.create_chat_invite_link(
                            chat_id,
                            member_limit=1,
                            expire_date=int(time.time()) + 7200  # 2 hours expiry
                        )

                        # Send invite to owner via DM
                        try:
                            invite_msg = f"""🔔 **Group Admin Invitation** (Attempt {retry_count + 1}/{max_retries})

You have been invited to join a group where I need to grant you admin permissions.

**Group:** {chat_id}
**Invite Link:** {invite_link.invite_link}

Please join the group so I can automatically grant you admin permissions for proper management.

This invitation expires in 2 hours."""

                            self.bot.send_message(owner_id, invite_msg, parse_mode='Markdown')
                            owners_invited.append(owner_id)
                            logger.info(f"Sent invite link to owner {owner_id} for group {chat_id} (attempt {retry_count + 1})")
                            return

                        except Exception as dm_error:
                            logger.warning(f"Could not send DM to owner {owner_id} (attempt {retry_count + 1}): {dm_error}")
                            retry_count += 1
                            if retry_count >= max_retries:
                                owners_failed_to_add.append(owner_id)
                                return
                            time.sleep(2)  # Wait before retry
                            continue

                    except Exception as invite_error:
                        logger.error(f"Could not create invite link for group {chat_id} (attempt {retry_count + 1}): {invite_error}")
                        retry_count += 1
                        if retry_count >= max_retries:
                            owners_failed_to_add.append(owner_id)
                            return
                        time.sleep(2)  # Wait before retry
                        continue

            except Exception as general_error:
                logger.error(f"General error inviting owner {owner_id} to group {chat_id} (attempt {retry_count + 1}): {general_error}")
                retry_count += 1
                if retry_count >= max_retries:
                    owners_failed_to_add.append(owner_id)
                    return
                time.sleep(2)  # Wait before retry

    def setup_handlers(self):
        @self.bot.message_handler(commands=['start'])
        def send_welcome(message):
            # Check if user has required membership
            if not self.check_user_membership(message.from_user.id):
                self.send_membership_required_message(message)
                return

            # Check if this is from group access
            if message.text.endswith('group_access'):
                markup = types.InlineKeyboardMarkup()
                activate_btn = types.InlineKeyboardButton("🚀 Activate Bot", callback_data="activate_bot")
                markup.add(activate_btn)

                activation_text = """
✅ **Membership Verified!**

You have successfully joined both our main channel and group. 

Click the button below to activate the bot and start using it in groups.
                """
                self.bot.send_message(message.chat.id, activation_text, reply_markup=markup, protect_content=self.should_protect_content(message.chat.id))
                return

            welcome_text = """
Welcome to Manual Movie Database Bot! 🎬

🗃️ **Manual Database System**:
• Send a text file to automatically add movies to database
• /search [movie title] - Search movies and get videos
• /database - Browse all movies in database

📝 **Text File Format**:
First line: IMDb URL
Following lines: Telegram video links

📺 **Search Features**:
• In groups: Shows button to get videos
• In private chat: Automatically forwards all videos

**How to add movies:**
Just send a text file containing:
```
https://www.imdb.com/title/tt1234567/
https://t.me/c/123456789/1
https://t.me/c/123456789/2
```

I use IMDb for movie information and forward your uploaded videos.
            """
            self.bot.reply_to(message, welcome_text, protect_content=self.should_protect_content(message.chat.id))

        @self.bot.message_handler(commands=['database'])
        def show_database(message):
            """Handle database command with waiting system"""
            def process_database(msg):
                # Check membership first
                if not self.check_user_membership(msg.from_user.id):
                    self.send_membership_required_message(msg)
                    return

                # Handle group interactions
                if not self.handle_group_interaction(msg):
                    return

                try:
                    # Load database
                    db = self.load_database()

                    if not db:
                        self.bot.reply_to(msg, "📁 Database is empty. Add movies by sending text files with IMDb URLs and video links.", protect_content=self.should_protect_content(msg.chat.id))
                        return

                    # Create inline keyboard with movie titles (max 10 per page for better UX)
                    markup = types.InlineKeyboardMarkup()

                    # Sort movies by title for better organization
                    sorted_movies = sorted(db.items(), key=lambda x: x[1]['title'].lower())

                    # Pagination logic
                    page_num = 1
                    items_per_page = 10
                    total_movies = len(db)

                    # Determine current page (if provided in callback data, otherwise default to 1)
                    if ':' in msg.text: # This is a callback query
                        parts = msg.text.split(':')
                        if len(parts) == 3 and parts[1] == 'page':
                            try:
                                page_num = int(parts[2])
                            except ValueError:
                                page_num = 1 # Default to page 1 if page number is invalid

                    start_index = (page_num - 1) * items_per_page
                    end_index = start_index + items_per_page

                    paginated_movies = sorted_movies[start_index:end_index]

                    if not paginated_movies:
                        self.bot.reply_to(msg, "No movies found on this page.", protect_content=self.should_protect_content(msg.chat.id))
                        return

                    for i, (imdb_id, movie_data) in enumerate(paginated_movies):
                        title = movie_data['title']
                        year = movie_data.get('year', 'N/A')
                        video_count = len(movie_data.get('video_links', []))

                        button_text = f"🎬 {title} ({year}) - {video_count} videos"
                        callback_data = f"db_movie:{imdb_id}"

                        button = types.InlineKeyboardButton(button_text, callback_data=callback_data)
                        markup.row(button)

                    # Add pagination buttons
                    pagination_markup = types.InlineKeyboardMarkup()
                    nav_buttons = []
                    if page_num > 1:
                        prev_button = types.InlineKeyboardButton("⬅️ Previous", callback_data=f"database:page:{page_num - 1}")
                        nav_buttons.append(prev_button)

                    page_info = f"Page {page_num} of {((total_movies - 1) // items_per_page) + 1}"
                    page_text_button = types.InlineKeyboardButton(page_info, callback_data="ignore") # Dummy button for spacing
                    nav_buttons.append(page_text_button)

                    if end_index < total_movies:
                        next_button = types.InlineKeyboardButton("Next ➡️", callback_data=f"database:page:{page_num + 1}")
                        nav_buttons.append(next_button)

                    if nav_buttons:
                        pagination_markup.row(*nav_buttons)

                    # Combine movie buttons and pagination buttons
                    if pagination_markup.keyboard:
                        markup.keyboard.extend(pagination_markup.keyboard)

                    # Display info text
                    info_text = f"📊 Showing {len(paginated_movies)} of {total_movies} movies"
                    self.bot.reply_to(msg, info_text, reply_markup=markup, protect_content=self.should_protect_content(msg.chat.id))

                except Exception as e:
                    logger.error(f"Error showing database: {e}")
                    self.bot.reply_to(msg, "❌ Error accessing database. Please try again.", protect_content=self.should_protect_content(msg.chat.id))

            # Add to waiting queue with 2-second delay
            self.add_to_queue(message, process_database)

        @self.bot.message_handler(commands=['help'])
        def send_help(message):
            # Check membership first
            if not self.check_user_membership(message.from_user.id):
                self.send_membership_required_message(message)
                return

            help_text = """
How to use this bot:

📁 **Adding Movies**:
- Send a text file (.txt) containing movie data
- Text file should contain IMDb URL on first line
- Following lines should have Telegram video links
- Add filter words with # symbol for better search
- Example format:
  ```
  https://www.imdb.com/title/tt1234567/
  https://t.me/c/123456789/1
  https://t.me/c/123456789/2
  #alternative name, local name, channel name
  ```

🔍 **Searching Movies**:
- /search [movie title]
- In groups: Shows button to get videos
- In private chat: Automatically forwards all videos

🗃️ **Browse Database**:
- /database - Browse all movies in database
- Click on any title to get all videos

📝 **Adding More Videos**:
- Reply to existing movie entry with new text file
- Upload text file with new video links
- They will be added to existing movie

Examples:
- /search The Matrix
- /database
- Send text file (automatically processed)
            """
            self.bot.reply_to(message, help_text, protect_content=self.should_protect_content(message.chat.id))

        @self.bot.message_handler(commands=['manage_admins'])
        def handle_manage_admins(message):
            """Handle admin management command"""
            # Check if user is owner
            if message.from_user.id not in self.owner_ids:
                self.bot.reply_to(message, "❌ This command is only available to bot owners.", protect_content=self.should_protect_content(message.chat.id))
                return

            # Check if used in a group
            if message.chat.type not in ['group', 'supergroup']:
                self.bot.reply_to(message, "❌ This command can only be used in groups.", protect_content=self.should_protect_content(message.chat.id))
                return

            try:
                # Cache this group
                self.save_group_to_cache(message.chat.id)

                # Get current admin status before management
                chat_members = self.bot.get_chat_administrators(message.chat.id)
                owners_before = []
                bot_can_promote = False
                bot_can_invite = False

                for member in chat_members:
                    if member.user.id == self.bot.get_me().id:
                        bot_can_promote = member.can_promote_members
                        bot_can_invite = member.can_invite_users
                    elif member.user.id in self.owner_ids:
                        owners_before.append(member.user.id)

                # Run manual admin management
                self.manage_group_admins_automatically(message.chat.id)

                # Get status after management
                chat_members_after = self.bot.get_chat_administrators(message.chat.id)
                owners_after = []

                for member in chat_members_after:
                    if member.user.id in self.owner_ids:
                        owners_after.append(member.user.id)

                # Create detailed status report
                result = "✅ **Admin Management Report**\n\n"
                result += f"**Bot Permissions:** {'✅ Can promote members' if bot_can_promote else '❌ Cannot promote members'}, {'✅ Can invite users' if bot_can_invite else '❌ Cannot invite users'}\n"
                result += f"**Owners before:** {len(owners_before)}/{len(self.owner_ids)}\n"
                result += f"**Owners after:** {len(owners_after)}/{len(self.owner_ids)}\n"

                if len(owners_after) > len(owners_before):
                    newly_added = len(owners_after) - len(owners_before)
                    result += f"**✅ Added {newly_added} owner(s) as admin(s)**\n"

                if len(owners_after) == len(self.owner_ids):
                    result += f"**🎉 All owners now have admin permissions!**\n"
                else:
                    missing = len(self.owner_ids) - len(owners_after)
                    result += f"**⚠️ {missing} owner(s) still missing (likely not in group or blocked bot)**\n"

                result += "\n**Automatic checking every 10 seconds is active.**"

                self.bot.reply_to(message, result, parse_mode='Markdown', protect_content=self.should_protect_content(message.chat.id))
            except Exception as e:
                logger.error(f"Error in manage_admins command: {e}")
                self.bot.reply_to(message, "❌ Error managing admin permissions.", protect_content=self.should_protect_content(message.chat.id))

        @self.bot.message_handler(content_types=['document'])
        def handle_document(message):
            """Automatically process text files when uploaded"""
            # Add to waiting queue instead of processing immediately
            def process_document(msg):
                # Check membership first
                if not self.check_user_membership(msg.from_user.id):
                    self.send_membership_required_message(msg)
                    return

                # Handle group interactions
                if not self.handle_group_interaction(msg):
                    return

                # Check if it's a text file
                if not msg.document.mime_type or 'text' not in msg.document.mime_type:
                    self.bot.reply_to(msg, "Please send a text file (.txt) containing IMDb URL and video links.", protect_content=self.should_protect_content(msg.chat.id))
                    return

                try:
                    # Send welcome message and pin it
                    welcome_msg = self.send_welcome_message(msg.chat.id)
                    try:
                        self.bot.pin_chat_message(msg.chat.id, welcome_msg.message_id)
                    except Exception as e:
                        logger.warning(f"Could not pin welcome message: {e}")

                    # Download the file
                    file_info = self.bot.get_file(msg.document.file_id)
                    if file_info.file_path:
                        downloaded_file = self.bot.download_file(file_info.file_path)
                    else:
                        self.bot.reply_to(msg, "❌ Could not download file. Please try again.", protect_content=self.should_protect_content(msg.chat.id))
                        return
                    content = downloaded_file.decode('utf-8')

                    # Parse the content
                    imdb_url, video_links, filter_words = self.parse_text_file_content(content)

                    if not imdb_url:
                        self.bot.reply_to(msg, "❌ No IMDb URL found in the file. Please include an IMDb URL on the first line.", protect_content=self.should_protect_content(msg.chat.id))
                        return

                    if not video_links:
                        self.bot.reply_to(msg, "❌ No video links found in the file. Please include Telegram video links.", protect_content=self.should_protect_content(msg.chat.id))
                        return

                    # Extract IMDb ID
                    imdb_id = self.extract_imdb_id_from_url(imdb_url)
                    if not imdb_id:
                        self.bot.reply_to(msg, "❌ Invalid IMDb URL format.", protect_content=self.should_protect_content(msg.chat.id))
                        return

                    # Get movie information
                    movie_info = self.get_movie_info_from_imdb(imdb_id)
                    if not movie_info:
                        self.bot.reply_to(msg, "❌ Could not fetch movie information from IMDb.", protect_content=self.should_protect_content(msg.chat.id))
                        return

                    # Load database
                    db = self.load_database()

                    # Check if this is an update (reply to existing entry)
                    if msg.reply_to_message and msg.reply_to_message.from_user.id == self.bot.get_me().id:
                        # This is an update - find existing movie and add videos
                        if imdb_id in db:
                            # Get existing video URLs for duplicate checking
                            existing_urls = set()
                            for existing_link in db[imdb_id]['video_links']:
                                # Handle both old format (string) and new format (dict)
                                url = existing_link['url'] if isinstance(existing_link, dict) else existing_link
                                existing_urls.add(url)

                            # Filter out duplicate video links
                            new_video_links = []
                            duplicate_count = 0
                            for video_link in video_links:
                                url = video_link['url'] if isinstance(video_link, dict) else video_link
                                if url not in existing_urls:
                                    new_video_links.append(video_link)
                                else:
                                    duplicate_count += 1

                            # Add only new videos to existing entry
                            if new_video_links:
                                db[imdb_id]['video_links'].extend(new_video_links)

                            # Update filter words (merge with existing ones)
                            if filter_words:
                                existing_filters = set(db[imdb_id].get('filter_words', []))
                                new_filters = set(filter_words)
                                db[imdb_id]['filter_words'] = list(existing_filters.union(new_filters))

                            if new_video_links or filter_words:
                                self.save_database(db)

                                # Create detailed success message
                                total_submitted = len(video_links)
                                new_added = len(new_video_links)
                                total_videos = len(db[imdb_id]['video_links'])

                                success_msg = f"✅ Update completed for '{movie_info['title']}' ({movie_info['year']})!\n"
                                success_msg += f"📊 **Processing Summary:**\n"
                                success_msg += f"• Total links submitted: {total_submitted}\n"
                                success_msg += f"• New links added: {new_added}\n"
                                success_msg += f"• Duplicates skipped: {duplicate_count}\n"
                                success_msg += f"• Total videos now: {total_videos}\n"
                                if filter_words:
                                    success_msg += f"• Filter words added: {len(filter_words)}\n"
                                success_msg += f"🔗 IMDb: {imdb_url}"

                                self.bot.reply_to(msg, success_msg, protect_content=self.should_protect_content(msg.chat.id))

                                # Send additional messages for updates
                                self.send_copyright_messages(msg.chat.id)
                                self.send_support_button(msg.chat.id)
                                self.send_thank_you_message(msg.chat.id)
                                return

                    # Check if movie already exists in database
                    if imdb_id in db:
                        # Get existing video URLs for duplicate checking
                        existing_urls = set()
                        for existing_link in db[imdb_id]['video_links']:
                            # Handle both old format (string) and new format (dict)
                            url = existing_link['url'] if isinstance(existing_link, dict) else existing_link
                            existing_urls.add(url)

                        # Filter out duplicate video links
                        new_video_links = []
                        duplicate_count = 0
                        for video_link in video_links:
                            url = video_link['url'] if isinstance(video_link, dict) else video_link
                            if url not in existing_urls:
                                new_video_links.append(video_link)
                            else:
                                duplicate_count += 1

                        # If there are new videos, add them automatically
                        if new_video_links:
                            db[imdb_id]['video_links'].extend(new_video_links)

                        # Update filter words (merge with existing ones)
                        if filter_words:
                            existing_filters = set(db[imdb_id].get('filter_words', []))
                            new_filters = set(filter_words)
                            db[imdb_id]['filter_words'] = list(existing_filters.union(new_filters))

                        if new_video_links or filter_words:
                            self.save_database(db)

                            # Create detailed success message for automatic update
                            total_submitted = len(video_links)
                            new_added = len(new_video_links)
                            total_videos = len(db[imdb_id]['video_links'])

                            success_msg = f"✅ Updated existing movie '{movie_info['title']}' ({movie_info['year']})!\n"
                            success_msg += f"📊 **Processing Summary:**\n"
                            success_msg += f"• Total links submitted: {total_submitted}\n"
                            success_msg += f"• New links added: {new_added}\n"
                            success_msg += f"• Duplicates skipped: {duplicate_count}\n"
                            success_msg += f"• Total videos now: {total_videos}\n"
                            if filter_words:
                                success_msg += f"• Filter words added: {len(filter_words)}\n"
                            success_msg += f"🔗 IMDb: {imdb_url}"

                            self.bot.reply_to(msg, success_msg, protect_content=self.should_protect_content(msg.chat.id))

                            # Send additional messages after successful upload
                            self.send_copyright_messages(msg.chat.id)
                            self.send_support_button(msg.chat.id)
                            self.send_thank_you_message(msg.chat.id)
                        else:
                            # All links are duplicates
                            duplicate_msg = f"📽️ Movie '{movie_info['title']}' already exists in database.\n"
                            duplicate_msg += f"📊 All {duplicate_count} submitted links are duplicates.\n"
                            duplicate_msg += f"📺 Current total: {len(db[imdb_id]['video_links'])} videos\n\n"
                            duplicate_msg += "To add more videos, send a text file with new video links that aren't already in the database."

                            self.bot.reply_to(msg, duplicate_msg, protect_content=self.should_protect_content(msg.chat.id))
                        return

                    # Add new movie to database
                    db[imdb_id] = {
                        'title': movie_info['title'],
                        'year': movie_info['year'],
                        'director': movie_info['director'],
                        'genre': movie_info['genre'],
                        'plot': movie_info['plot'],
                        'rating': movie_info['rating'],
                        'imdb_url': imdb_url,
                        'video_links': video_links,
                        'filter_words': filter_words,
                        'added_date': datetime.now().isoformat()
                    }

                    self.save_database(db)

                    success_msg = f"✅ Successfully added '{movie_info['title']}' ({movie_info['year']}) to database!\n"
                    success_msg += f"📺 Videos: {len(video_links)}\n"
                    if filter_words:
                        success_msg += f"🏷️ Filter words: {len(filter_words)}\n"
                    success_msg += f"🔗 IMDb: {imdb_url}"

                    self.bot.reply_to(msg, success_msg, protect_content=self.should_protect_content(msg.chat.id))

                    # Send copyright and support messages after successful upload
                    self.send_copyright_messages(msg.chat.id)
                    self.send_support_button(msg.chat.id)
                    self.send_thank_you_message(msg.chat.id)

                except Exception as e:
                    logger.error(f"Error processing document: {e}")
                    self.bot.reply_to(msg, "❌ Error processing the file. Please check the format and try again.", protect_content=self.should_protect_content(msg.chat.id))

            # Add to waiting queue with 2-second delay
            self.add_to_queue(message, process_document)

        @self.bot.message_handler(commands=['search'])
        def search_movie_command(message):
            """Handle search command with waiting system"""
            def process_search(msg):
                # Check membership first
                if not self.check_user_membership(msg.from_user.id):
                    self.send_membership_required_message(msg)
                    return

                # Handle group interactions
                if not self.handle_group_interaction(msg):
                    return

                # Extract movie title from command
                movie_title_query = msg.text.replace('/search', '').strip()
                if not movie_title_query:
                    self.bot.reply_to(msg, "Please provide a movie title. Example: /search The Matrix", protect_content=self.should_protect_content(msg.chat.id))
                    return

                try:
                    # Search using IMDbPY
                    self.bot.reply_to(msg, f"🔍 Searching for '{movie_title_query}'...", protect_content=self.should_protect_content(msg.chat.id))

                    # Search for movies using IMDbPY
                    search_results = self.ia.search_movie(movie_title_query)

                    if not search_results:
                        self.bot.reply_to(msg, f"❌ No movies found for '{movie_title_query}' on IMDb.", protect_content=self.should_protect_content(msg.chat.id))
                        return

                    # Get the first (most relevant) result
                    movie = search_results[0]
                    movie_id = movie.movieID

                    # Get detailed movie information
                    detailed_movie = self.ia.get_movie(movie_id)

                    # Extract information safely
                    title = detailed_movie.get('title', 'Unknown')
                    year = detailed_movie.get('year', 'N/A')
                    directors = detailed_movie.get('directors', [])
                    director_str = ', '.join([str(d) for d in directors]) if directors else 'N/A'
                    genres = detailed_movie.get('genres', [])
                    genre_str = ', '.join(genres) if genres else 'N/A'
                    plot_data = detailed_movie.get('plot outline') or detailed_movie.get('plot')
                    if plot_data:
                        plot = plot_data[0] if isinstance(plot_data, list) else plot_data
                    else:
                        plot = 'N/A'
                    rating = detailed_movie.get('rating', 'N/A')

                    # Construct IMDb URL
                    imdb_id = f"tt{movie_id}"
                    imdb_url = f"https://www.imdb.com/title/{imdb_id}/"

                    # Format movie information exactly as requested
                    movie_info_msg = f"""🎬 Title: {title} ({year})
⭐ IMDb Rating: {rating}
📀 Genre: {genre_str}
🎯 Director: {director_str}
🎭 Cast: N/A
📝 Plot: {plot}
🔗 IMDb: {imdb_url}"""

                    self.bot.send_message(msg.chat.id, movie_info_msg, protect_content=self.should_protect_content(msg.chat.id))

                    # Check if we have videos for this movie in our database
                    db = self.load_database()
                    found_videos = None
                    found_movie_data = None
                    matched_imdb_id = None

                    if imdb_id in db:
                        found_videos = db[imdb_id]['video_links']
                        found_movie_data = db[imdb_id]
                        matched_imdb_id = imdb_id
                    else:
                        # Search by filter words if not found by IMDb ID
                        search_terms = movie_title_query.lower().split()
                        for db_imdb_id, movie_data in db.items():
                            filter_words = movie_data.get('filter_words', [])
                            for filter_word in filter_words:
                                for search_term in search_terms:
                                    if search_term in filter_word:
                                        found_videos = movie_data['video_links']
                                        found_movie_data = movie_data
                                        matched_imdb_id = db_imdb_id

                                        # Override movie info with database info for filter matches
                                        title = movie_data['title']
                                        year = movie_data['year']
                                        rating = movie_data['rating']
                                        genre_str = movie_data['genre']
                                        director_str = movie_data['director']
                                        plot = movie_data['plot']
                                        imdb_url = movie_data['imdb_url']

                                        # Send updated movie info for filter match
                                        movie_info_msg = f"""🎬 Title: {title} ({year})
⭐ IMDb Rating: {rating}
📀 Genre: {genre_str}
🎯 Director: {director_str}
🎭 Cast: N/A
📝 Plot: {plot}
🔗 IMDb: {imdb_url}

🏷️ Found via filter: "{filter_word}" """

                                        self.bot.send_message(msg.chat.id, movie_info_msg, protect_content=self.should_protect_content(msg.chat.id))
                                        break
                                if found_videos:
                                    break
                            if found_videos:
                                break

                    if found_videos:
                        # Check if private chat or group
                        if msg.chat.type == 'private':
                            # Private chat - automatically forward all videos
                            success_count = 0
                            for video_link in found_videos:
                                # Handle both old format (string) and new format (dict)
                                url = video_link['url'] if isinstance(video_link, dict) else video_link
                                if self.forward_video_by_url(msg.chat.id, url):
                                    success_count += 1
                                # Add delay between video forwards to prevent bot blocking
                                time.sleep(1.5)

                            final_msg = f"📺 Forwarded {success_count}/{len(found_videos)} videos for '{title}'."
                            self.bot.send_message(msg.chat.id, final_msg, protect_content=self.should_protect_content(msg.chat.id))

                            # Send additional messages after video forwarding
                            self.send_copyright_messages(msg.chat.id)
                            self.send_support_button(msg.chat.id)
                            self.send_thank_you_message(msg.chat.id)

                        else:
                            # Group chat - show button
                            markup = types.InlineKeyboardMarkup()
                            # Add emoji to the button text
                            get_button = types.InlineKeyboardButton("▶️ Get Videos", callback_data=f"get_videos:{matched_imdb_id}")
                            markup.add(get_button)

                            button_msg = f"🎬 Found: {title} ({year})\n📺 {len(found_videos)} videos available"
                            self.bot.send_message(msg.chat.id, button_msg, reply_markup=markup, protect_content=self.should_protect_content(msg.chat.id))
                    else:
                        self.bot.send_message(msg.chat.id, "📺 No videos available for this movie in our database.", protect_content=self.should_protect_content(msg.chat.id))

                except Exception as e:
                    logger.error(f"Error searching for movie '{movie_title_query}': {e}")
                    self.bot.reply_to(msg, f"❌ Error searching for '{movie_title_query}'. Please try again.", protect_content=self.should_protect_content(msg.chat.id))

            # Add to waiting queue with 2-second delay
            self.add_to_queue(message, process_search)

        @self.bot.callback_query_handler(func=lambda call: call.data == 'check_membership')
        def handle_check_membership(call):
            # Re-check membership
            if self.check_user_membership(call.from_user.id):
                success_text = """
✅ **Membership Verified!**

Great! You have successfully joined both our main channel and group.

You can now use all bot features. Try /search to find movies or send a text file to add new movies to the database.
                """
                self.bot.edit_message_text(success_text, call.message.chat.id, call.message.message_id)
                self.bot.answer_callback_query(call.id, "✅ Access granted!")
            else:
                self.bot.answer_callback_query(call.id, "❌ Please join both channel and group first", show_alert=True)

        @self.bot.callback_query_handler(func=lambda call: call.data == 'activate_bot')
        def handle_activate_bot(call):
            activation_complete_text = """
🚀 **Bot Activated Successfully!**

You can now use the bot in:
• Groups and channels
• Private chats
• All available features

Try these commands:
• /search [movie name] - Search for movies
• Send text files to add movies
• Send movie posters for recognition

Enjoy using the Manual Movie Database Bot! 🎬
            """
            self.bot.edit_message_text(activation_complete_text, call.message.chat.id, call.message.message_id)
            self.bot.answer_callback_query(call.id, "🚀 Bot activated successfully!")

        @self.bot.callback_query_handler(func=lambda call: call.data.startswith('database:page:'))
        def handle_database_pagination(call):
            """Handles pagination for the /database command."""
            try:
                page_num = int(call.data.split(':')[2])
                # Re-trigger the database command with the specified page number
                # We need to simulate a message object to pass to the handler
                class MockMessage:
                    def __init__(self, chat, from_user, text):
                        self.chat = chat
                        self.from_user = from_user
                        self.text = text
                        self.reply_to_message = None # For cases where it's not a reply

                mock_message = MockMessage(
                    chat=call.message.chat,
                    from_user=call.from_user,
                    text=f"/database:page:{page_num}" # Pass page info in text
                )
                # Call the original process_database handler indirectly
                # Need to get the handler from the closure
                # This part is a bit tricky due to how handlers are defined within handlers.
                # A simpler approach is to directly call the logic here.

                # Load database
                db = self.load_database()

                if not db:
                    self.bot.edit_message_text("📁 Database is empty. Add movies by sending text files with IMDb URLs and video links.", call.message.chat.id, call.message.message_id)
                    self.bot.answer_callback_query(call.id, "Database is empty.")
                    return

                markup = types.InlineKeyboardMarkup()
                sorted_movies = sorted(db.items(), key=lambda x: x[1]['title'].lower())

                items_per_page = 10
                total_movies = len(db)

                start_index = (page_num - 1) * items_per_page
                end_index = start_index + items_per_page

                paginated_movies = sorted_movies[start_index:end_index]

                if not paginated_movies:
                    self.bot.edit_message_text("No movies found on this page.", call.message.chat.id, call.message.message_id)
                    self.bot.answer_callback_query(call.id, "No movies on this page.")
                    return

                for i, (imdb_id, movie_data) in enumerate(paginated_movies):
                    title = movie_data['title']
                    year = movie_data.get('year', 'N/A')
                    video_count = len(movie_data.get('video_links', []))

                    button_text = f"🎬 {title} ({year}) - {video_count} videos"
                    callback_data = f"db_movie:{imdb_id}"

                    button = types.InlineKeyboardButton(button_text, callback_data=callback_data)
                    markup.row(button)

                # Add pagination buttons
                pagination_markup = types.InlineKeyboardMarkup()
                nav_buttons = []
                if page_num > 1:
                    prev_button = types.InlineKeyboardButton("⬅️ Previous", callback_data=f"database:page:{page_num - 1}")
                    nav_buttons.append(prev_button)

                page_info = f"Page {page_num} of {((total_movies - 1) // items_per_page) + 1}"
                page_text_button = types.InlineKeyboardButton(page_info, callback_data="ignore") 
                nav_buttons.append(page_text_button)

                if end_index < total_movies:
                    next_button = types.InlineKeyboardButton("Next ➡️", callback_data=f"database:page:{page_num + 1}")
                    nav_buttons.append(next_button)

                if nav_buttons:
                    pagination_markup.row(*nav_buttons)

                if pagination_markup.keyboard:
                    markup.keyboard.extend(pagination_markup.keyboard)

                info_text = f"📊 Showing {len(paginated_movies)} of {total_movies} movies"
                self.bot.edit_message_text(info_text, call.message.chat.id, call.message.message_id, reply_markup=markup)
                self.bot.answer_callback_query(call.id, f"Switched to page {page_num}")

            except Exception as e:
                logger.error(f"Error handling database pagination: {e}")
                self.bot.answer_callback_query(call.id, "Error changing page.")


        @self.bot.callback_query_handler(func=lambda call: call.data.startswith('db_movie:'))
        def handle_database_movie_selection(call):
            try:
                # Extract IMDb ID from callback data
                imdb_id = call.data.replace('db_movie:', '')

                # Load database and find movie
                db = self.load_database()

                if imdb_id not in db:
                    self.bot.answer_callback_query(call.id, "Movie not found in database.")
                    return

                movie_data = db[imdb_id]
                video_links = movie_data['video_links']

                if not video_links:
                    self.bot.answer_callback_query(call.id, "No videos found for this movie.")
                    return

                # Show movie info first
                movie_info_msg = f"""🎬 **{movie_data['title']}** ({movie_data.get('year', 'N/A')})
⭐ IMDb Rating: {movie_data.get('rating', 'N/A')}
🎭 Genre: {movie_data.get('genre', 'N/A')}
🎯 Director: {movie_data.get('director', 'N/A')}
📝 Plot: {movie_data.get('plot', 'N/A')[:200]}{'...' if len(movie_data.get('plot', '')) > 200 else ''}
🔗 IMDb: {movie_data['imdb_url']}

📺 Sending {len(video_links)} videos..."""

                # Send movie info to user's private chat
                try:
                    self.bot.send_message(call.from_user.id, movie_info_msg, parse_mode='Markdown', protect_content=self.should_protect_content(call.from_user.id))
                except:
                    # If can't send to private chat, send to current chat
                    self.bot.send_message(call.message.chat.id, movie_info_msg, parse_mode='Markdown', protect_content=self.should_protect_content(call.message.chat.id))

                # Forward all videos to the user who clicked the button
                success_count = 0
                for video_link in video_links:
                    # Handle both old format (string) and new format (dict)
                    url = video_link['url'] if isinstance(video_link, dict) else video_link
                    if self.forward_video_by_url(call.from_user.id, url):
                        success_count += 1
                    # Add delay between video forwards to prevent bot blocking
                    time.sleep(1.5)

                self.bot.answer_callback_query(call.id, f"Sent {success_count}/{len(video_links)} videos to your private chat!")

                # Send completion message to user's private chat
                try:
                    final_msg = f"✅ Forwarded {success_count}/{len(video_links)} videos for '{movie_data['title']}'."
                    self.bot.send_message(call.from_user.id, final_msg, protect_content=self.should_protect_content(call.from_user.id))

                    # Send additional messages after video forwarding
                    self.send_copyright_messages(call.from_user.id)
                    self.send_support_button(call.from_user.id)
                    self.send_thank_you_message(call.from_user.id)
                except:
                    pass  # User might not have started the bot

            except Exception as e:
                logger.error(f"Error handling database movie selection: {e}")
                self.bot.answer_callback_query(call.id, "Error retrieving videos.")

        @self.bot.callback_query_handler(func=lambda call: call.data.startswith('get_videos:'))
        def handle_get_videos(call):
            try:
                # Extract IMDb ID from callback data
                imdb_id = call.data.replace('get_videos:', '')

                # Load database and find movie
                db = self.load_database()

                if imdb_id not in db:
                    self.bot.answer_callback_query(call.id, "Movie not found in database.")
                    return

                movie_data = db[imdb_id]
                video_links = movie_data['video_links']

                if not video_links:
                    self.bot.answer_callback_query(call.id, "No videos found for this movie.")
                    return

                # Forward all videos to the user who clicked the button
                success_count = 0
                for video_link in video_links:
                    # Handle both old format (string) and new format (dict)
                    url = video_link['url'] if isinstance(video_link, dict) else video_link
                    if self.forward_video_by_url(call.from_user.id, url):
                        success_count += 1
                    # Add delay between video forwards to prevent bot blocking
                    time.sleep(1.5)

                self.bot.answer_callback_query(call.id, f"Sent {success_count}/{len(video_links)} videos to your private chat!")

                # Send completion message to user's private chat
                try:
                    final_msg = f"✅ Forwarded {success_count}/{len(video_links)} videos for '{movie_data['title']}'."
                    self.bot.send_message(call.from_user.id, final_msg, protect_content=self.should_protect_content(call.from_user.id))

                    # Send additional messages after video forwarding
                    self.send_copyright_messages(call.from_user.id)
                    self.send_support_button(call.from_user.id)
                    self.send_thank_you_message(call.from_user.id)
                except:
                    pass  # User might not have started the bot

            except Exception as e:
                logger.error(f"Error handling get videos: {e}")
                self.bot.answer_callback_query(call.id, "Error retrieving videos.")

        @self.bot.message_handler(content_types=['photo'])
        def handle_photo(message):
            """Handle photo messages with waiting system"""
            def process_photo(msg):
                # Check membership first
                if not self.check_user_membership(msg.from_user.id):
                    self.send_membership_required_message(msg)
                    return

                # Handle group interactions
                if not self.handle_group_interaction(msg):
                    return

                # Handle photo messages for poster recognition
                self.analyze_movie_poster(msg)

            # Add to waiting queue with 2-second delay
            self.add_to_queue(message, process_photo)

        @self.bot.message_handler(func=lambda message: True)
        def handle_text_search(message):
            """Handle text search with waiting system"""
            def process_text_search(msg):
                # Check membership first
                if not self.check_user_membership(msg.from_user.id):
                    self.send_membership_required_message(msg)
                    return

                # Handle group interactions
                if not self.handle_group_interaction(msg):
                    return

                # Treat any text message as a movie search in private chats
                if msg.chat.type == 'private':
                    movie_title = msg.text.strip()

                    # Search in database
                    db = self.load_database()
                    found_movie = None
                    found_imdb_id = None

                    # Search for movie (case-insensitive)
                    title_lower = movie_title.lower()
                    for imdb_id, movie_data in db.items():
                        if title_lower in movie_data['title'].lower():
                            found_movie = movie_data
                            found_imdb_id = imdb_id
                            break

                    # If not found by title, search by filter words
                    if not found_movie:
                        search_terms = title_lower.split()
                        for imdb_id, movie_data in db.items():
                            filter_words = movie_data.get('filter_words', [])
                            for filter_word in filter_words:
                                for search_term in search_terms:
                                    if search_term in filter_word:
                                        found_movie = movie_data
                                        found_imdb_id = imdb_id
                                        break
                                if found_movie:
                                    break
                            if found_movie:
                                break

                    if found_movie:
                        # Send IMDb link
                        imdb_msg = f"🔗 IMDb: {found_movie['imdb_url']}"
                        self.bot.reply_to(msg, imdb_msg, protect_content=self.should_protect_content(msg.chat.id))

                        # Automatically forward all videos
                        video_links = found_movie['video_links']
                        success_count = 0

                        for video_link in video_links:
                            # Handle both old format (string) and new format (dict)
                            url = video_link['url'] if isinstance(video_link, dict) else video_link
                            if self.forward_video_by_url(msg.chat.id, url):
                                success_count += 1
                            # Add delay between video forwards to prevent bot blocking
                            time.sleep(1.5)

                        final_msg = f"📺 Forwarded {success_count}/{len(video_links)} videos for '{found_movie['title']}'."
                        self.bot.send_message(msg.chat.id, final_msg, protect_content=self.should_protect_content(msg.chat.id))

                        # Send additional messages after video forwarding
                        self.send_copyright_messages(msg.chat.id)
                        self.send_support_button(msg.chat.id)
                        self.send_thank_you_message(msg.chat.id)
                    else:
                        self.bot.reply_to(msg, f"Movie '{movie_title}' not found in database.", protect_content=self.should_protect_content(msg.chat.id))

            # Add to waiting queue with 2-second delay
            self.add_to_queue(message, process_text_search)

    def analyze_movie_poster(self, message):
        """Analyze uploaded movie poster using OpenAI Vision API"""
        if not self.openai_client:
            self.bot.reply_to(message, "🚫 Image recognition is not available. OpenAI API key is required.", protect_content=self.should_protect_content(message.chat.id))
            return

        try:
            # Send analyzing message
            analyzing_msg = self.bot.reply_to(message, "🔍 Analyzing movie poster...", protect_content=self.should_protect_content(message.chat.id))

            # Get the best available photo (try highest resolution first, fallback to lower)
            photo = message.photo[-1] if message.photo else None
            if not photo:
                self.bot.reply_to(message, "❌ No photo found in message.", protect_content=self.should_protect_content(message.chat.id))
                return

            # Download the photo
            file_info = self.bot.get_file(photo.file_id)
            if not file_info.file_path:
                self.bot.edit_message_text("❌ Could not download the image. Please try again.", message.chat.id, analyzing_msg.message_id)
                return
            downloaded_file = self.bot.download_file(file_info.file_path)

            # Convert to base64 for OpenAI API
            base64_image = base64.b64encode(downloaded_file).decode('utf-8')

            # Analyze the image using OpenAI Vision
            movie_title = self.recognize_movie_poster(base64_image)

            if movie_title:
                # Update analyzing message
                self.bot.edit_message_text(
                    f"🎬 Found movie: '{movie_title}'\n🔍 Searching in database...", 
                    message.chat.id, 
                    analyzing_msg.message_id
                )

                # Search in database
                db = self.load_database()
                found_movie = None
                found_imdb_id = None

                title_lower = movie_title.lower()
                for imdb_id, movie_data in db.items():
                    if title_lower in movie_data['title'].lower():
                        found_movie = movie_data
                        found_imdb_id = imdb_id
                        break

                # If not found by title, search by filter words
                if not found_movie:
                    search_terms = title_lower.split()
                    for imdb_id, movie_data in db.items():
                        filter_words = movie_data.get('filter_words', [])
                        for filter_word in filter_words:
                            for search_term in search_terms:
                                if search_term in filter_word:
                                    found_movie = movie_data
                                    found_imdb_id = imdb_id
                                    break
                            if found_movie:
                                break
                        if found_movie:
                            break

                if found_movie:
                    # Delete analyzing message
                    self.bot.delete_message(message.chat.id, analyzing_msg.message_id)

                    # Send IMDb link
                    imdb_msg = f"🔗 IMDb: {found_movie['imdb_url']}"
                    self.bot.reply_to(message, imdb_msg, protect_content=self.should_protect_content(message.chat.id))

                    # Handle based on chat type
                    if message.chat.type == 'private':
                        # Automatically forward all videos
                        video_links = found_movie['video_links']
                        success_count = 0

                        for video_link in video_links:
                            # Handle both old format (string) and new format (dict)
                            url = video_link['url'] if isinstance(video_link, dict) else video_link
                            if self.forward_video_by_url(message.chat.id, url):
                                success_count += 1
                            # Add delay between video forwards to prevent bot blocking
                            time.sleep(1.5)

                        final_msg = f"📺 Forwarded {success_count}/{len(found_movie['video_links'])} videos for '{found_movie['title']}'."
                        self.bot.send_message(message.chat.id, final_msg, protect_content=self.should_protect_content(message.chat.id))

                        # Send additional messages after video forwarding
                        self.send_copyright_messages(message.chat.id)
                        self.send_support_button(message.chat.id)
                        self.send_thank_you_message(message.chat.id)
                    else:
                        # Group chat - show button
                        markup = types.InlineKeyboardMarkup()
                        # Add emoji to the button text
                        get_button = types.InlineKeyboardButton("▶️ Get Videos", callback_data=f"get_videos:{found_imdb_id}")
                        markup.add(get_button)

                        button_msg = f"🎬 Found: {found_movie['title']} ({found_movie['year']})\n📺 {len(found_movie['video_links'])} videos available"
                        self.bot.send_message(message.chat.id, button_msg, reply_markup=markup, protect_content=self.should_protect_content(message.chat.id))
                else:
                    self.bot.edit_message_text(
                        f"🎬 Recognized '{movie_title}' but it's not in our database.",
                        message.chat.id,
                        analyzing_msg.message_id
                    )

        except Exception as e:
            logger.error(f"Error analyzing poster: {e}")
            self.bot.reply_to(message, "❌ Error analyzing poster. Please try again or send the movie title as text.", protect_content=self.should_protect_content(message.chat.id))

    def recognize_movie_poster(self, base64_image):
        """Use OpenAI Vision to recognize movie title from poster"""
        if not self.openai_client:
            logger.error("OpenAI client not available")
            return None

        try:
            response = self.openai_client.chat.completions.create(
                model="gpt-4-vision-preview",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Analyze this movie poster and identify the movie title. Look for text on the poster, movie logos, recognizable characters, actors, or visual elements. Return ONLY the movie title, nothing else. If you cannot identify the movie, return 'UNKNOWN'."
                            },
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}
                            }
                        ]
                    }
                ],
                max_tokens=50
            )

            if not response or not response.choices or not response.choices[0].message.content:
                logger.error("OpenAI returned empty response")
                return None

            result = response.choices[0].message.content.strip()

            if result and result.upper() != 'UNKNOWN':
                logger.info(f"OpenAI recognized movie: {result}")
                return result
            else:
                logger.info("OpenAI could not recognize the movie poster")
                return None

        except Exception as e:
            logger.error(f"OpenAI Vision error: {e}")
            return None

    def run(self):
        """Start the bot"""
        logger.info("Starting Manual Movie Database Bot...")
        try:
            # Start the bot polling
            self.bot.infinity_polling()
        except Exception as e:
            logger.error(f"Bot error: {e}")
            raise

if __name__ == "__main__":
    bot = MovieBot()
    bot.run()
