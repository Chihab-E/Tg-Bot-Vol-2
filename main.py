import os
import re
import logging
import requests
import hashlib
import hmac
import time
import json
from urllib.parse import quote, parse_qs, urlparse
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class AliExpressAPI:
    def __init__(self, app_key, app_secret, tracking_id):
        self.app_key = app_key
        self.app_secret = app_secret
        self.tracking_id = tracking_id
        self.api_url = "https://api-sg.aliexpress.com/sync"
    
    def generate_signature(self, params, api_name):
        """Generate API signature for AliExpress API"""
        # Sort parameters
        sorted_params = sorted(params.items())
        
        # Create query string
        query_string = api_name
        for key, value in sorted_params:
            query_string += f"{key}{value}"
        query_string += self.app_secret
        
        # Generate signature
        signature = hmac.new(
            self.app_secret.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest().upper()
        
        return signature
    
    def get_affiliate_link(self, product_url, promotion_link_type="2"):
        """
        Get affiliate link for a product
        promotion_link_type: "0" for PC, "2" for mobile
        """
        # First resolve any short URLs to get the actual product URL
        resolved_url = self.resolve_short_url(product_url)
        
        # Extract product ID from resolved URL
        product_id = self.extract_product_id(resolved_url)
        if not product_id:
            return None
        
        # Create a clean product URL for the API
        clean_url = f"https://www.aliexpress.com/item/{product_id}.html"
        
        # Prepare API parameters
        timestamp = str(int(time.time() * 1000))
        api_name = "aliexpress.affiliate.link.generate"
        
        params = {
            "app_key": self.app_key,
            "method": api_name,
            "sign_method": "sha256",
            "timestamp": timestamp,
            "v": "2.0",
            "format": "json",
            "promotion_link_type": promotion_link_type,
            "source_values": clean_url,
            "tracking_id": self.tracking_id
        }
        
        # Generate signature
        signature = self.generate_signature(params, api_name)
        params["sign"] = signature
        
        try:
            response = requests.post(self.api_url, data=params, timeout=10)
            response.raise_for_status()
            
            result = response.json()
            
            if "aliexpress_affiliate_link_generate_response" in result:
                resp_data = result["aliexpress_affiliate_link_generate_response"]
                if "resp_result" in resp_data:
                    resp_result = resp_data["resp_result"]
                    if "result" in resp_result and "promotion_links" in resp_result["result"]:
                        promotion_links = resp_result["result"]["promotion_links"]
                        if promotion_links and len(promotion_links) > 0:
                            return promotion_links[0]["promotion_link"]
            
            logger.error(f"API Error: {result}")
            return None
            
        except Exception as e:
            logger.error(f"API request failed: {e}")
            return None
    
    def resolve_short_url(self, url):
        """Resolve short/affiliate URLs to get the actual product URL"""
        try:
            # Handle s.click.aliexpress.com and other short URLs
            if 's.click.aliexpress.com' in url or 'bit.ly' in url or 'tinyurl.com' in url:
                response = requests.head(url, allow_redirects=True, timeout=10)
                return response.url
            return url
        except Exception as e:
            logger.error(f"Error resolving short URL {url}: {e}")
            return url
    
    def extract_product_id(self, url):
        """Extract product ID from AliExpress URL (including affiliate links)"""
        # First, resolve any short URLs
        resolved_url = self.resolve_short_url(url)
        logger.info(f"Resolved URL: {resolved_url}")
        
        # Patterns to extract product ID from various URL formats
        patterns = [
            # Standard product pages
            r'/item/(\d+)\.html',
            r'productIds=(\d+)',
            r'/(\d+)\.html',
            r'product/(\d+)',
            # Mobile URLs
            r'm\.aliexpress\.com/item/(\d+)',
            r'm\.aliexpress\.com.*?(\d{10,})',
            # Affiliate URLs after resolution
            r'aliexpress\.com/item/(\d+)',
            r'aliexpress\.com.*?(\d{10,})',
            # Deep links and app links
            r'productId[=:](\d+)',
            r'itemId[=:](\d+)',
            # Extract from any URL containing long numbers (likely product IDs)
            r'(\d{10,})'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, resolved_url)
            if match:
                product_id = match.group(1)
                # Validate that it looks like a product ID (10+ digits)
                if len(product_id) >= 10:
                    return product_id
        
        # If no pattern matches, try to extract from original URL too
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                product_id = match.group(1)
                if len(product_id) >= 10:
                    return product_id
        
        return None
    
    def create_coin_discount_link(self, affiliate_link, product_id):
        """Create mobile coin discount link"""
        if not affiliate_link or not product_id:
            return None
        
        # Parse affiliate link to get parameters
        parsed_url = urlparse(affiliate_link)
        query_params = parse_qs(parsed_url.query)
        
        # Extract necessary parameters
        aff_fcid = query_params.get('aff_fcid', [''])[0]
        aff_fsk = query_params.get('aff_fsk', [''])[0]
        aff_trace_key = query_params.get('aff_trace_key', [''])[0]
        terminal_id = query_params.get('terminal_id', [''])[0]
        
        # Create coin discount URL
        coin_url = (
            f"https://m.aliexpress.com/p/coin-index/index.html"
            f"?_immersiveMode=true"
            f"&from=syicon"
            f"&productIds={product_id}"
            f"&aff_fcid={aff_fcid}"
            f"&tt=CPS_NORMAL"
            f"&aff_fsk={aff_fsk}"
            f"&aff_platform=portals-tool"
            f"&sk={aff_fsk}"
            f"&aff_trace_key={aff_trace_key}"
            f"&terminal_id={terminal_id}"
        )
        
        return coin_url

class TelegramBot:
    def __init__(self, bot_token, aliexpress_api):
        self.bot_token = bot_token
        self.api = aliexpress_api
        self.app = Application.builder().token(bot_token).build()
        
        # Add handlers
        self.app.add_handler(CommandHandler("start", self.start_command))
        self.app.add_handler(CommandHandler("help", self.help_command))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        welcome_message = """
🛍️ **AliExpress Coin Discount Bot**

Send me any AliExpress product link and I'll convert it to a mobile coin discount link!

**How to use:**
1. Copy any AliExpress product link
2. Send it to me
3. Get your coin discount link instantly!

**Features:**
✅ Automatic link conversion
✅ Mobile coin discounts
✅ Fast processing
✅ Works with all AliExpress products

Just send me a link to get started! 🚀
        """
        await update.message.reply_text(welcome_message, parse_mode='Markdown')
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
        help_message = """
🔧 **How to use this bot:**

1. **Find a product** on AliExpress (or get a link from someone else)
2. **Copy the link** (any AliExpress product URL)
3. **Send it here** - just paste and send
4. **Get your coin discount link** with YOUR affiliate tracking!

**✅ Supported link formats:**
• `https://www.aliexpress.com/item/...` - Regular product links
• `https://m.aliexpress.com/item/...` - Mobile links
• `https://s.click.aliexpress.com/e/...` - **Other people's affiliate links**
• Short links and redirects
• Any AliExpress product URL

**🔄 What happens to affiliate links:**
When you send someone else's affiliate link, the bot will:
• Extract the product information
• Generate a NEW link with YOUR affiliate tracking
• Convert it to a coin discount format
• You earn the commission instead! 💰

**💡 Example:**
**Send:** `https://s.click.aliexpress.com/e/_onrOEQB`
**Get:** Mobile coin discount link with YOUR tracking ID

**🎯 Pro Tips:**
• Works with any AliExpress link format
• Automatically handles redirects
• Converts competitor affiliate links
• Provides extra coin discounts for users

**Need help?** Just send /help anytime!
        """
        await update.message.reply_text(help_message, parse_mode='Markdown')
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle incoming messages with URLs"""
        message_text = update.message.text
        
        # Enhanced patterns to detect various AliExpress links including affiliate links
        aliexpress_patterns = [
            # Standard AliExpress URLs
            r'https?://(?:www\.)?aliexpress\.com/item/\d+\.html[^\s]*',
            r'https?://(?:www\.)?aliexpress\.com/item/[^/\s]+[^\s]*',
            r'https?://m\.aliexpress\.com/item/\d+\.html[^\s]*',
            r'https?://m\.aliexpress\.com/[^\s]*',
            # Short and affiliate links
            r'https?://s\.click\.aliexpress\.com/e/[^/\s]+',
            r'https?://(?:www\.)?aliexpress\.com/[^\s]*',
            r'https?://[^/]*aliexpress[^/]*\.com/[^\s]*',
            # Mobile coin index links (already converted)
            r'https?://m\.aliexpress\.com/p/coin-index/[^\s]*',
            # Any URL containing aliexpress
            r'https?://[^\s]*aliexpress[^\s]*'
        ]
        
        found_url = None
        for pattern in aliexpress_patterns:
            match = re.search(pattern, message_text)
            if match:
                found_url = match.group(0).rstrip('.,;!?')  # Remove trailing punctuation
                break
        
        if not found_url:
            await update.message.reply_text(
                "❌ No AliExpress link found!\n\n"
                "Please send a valid AliExpress product link.\n"
                "📝 **Supported formats:**\n"
                "• Regular product links\n"
                "• Mobile links\n"
                "• Affiliate links (s.click.aliexpress.com)\n"
                "• Short links\n\n"
                "**Example:** https://www.aliexpress.com/item/1005004633663909.html",
                parse_mode='Markdown'
            )
            return
        
        # Check if it's already a coin discount link
        if 'coin-index' in found_url:
            await update.message.reply_text(
                "ℹ️ This link is already a coin discount link!\n\n"
                "You can use it directly to get coin discounts. 🪙"
            )
            return
        
        # Show processing message
        processing_msg = await update.message.reply_text("🔄 Processing your link...")
        
        try:
            # Log original URL for debugging
            logger.info(f"Processing URL: {found_url}")
            
            # Get product ID (this will handle affiliate links and redirects)
            product_id = self.api.extract_product_id(found_url)
            if not product_id:
                await processing_msg.edit_text(
                    "❌ Could not extract product ID from the link.\n\n"
                    "Please make sure you're sending a valid AliExpress product link."
                )
                return
            
            logger.info(f"Extracted Product ID: {product_id}")
            
            # Get affiliate link with YOUR tracking ID
            affiliate_link = self.api.get_affiliate_link(found_url, promotion_link_type="2")
            if not affiliate_link:
                await processing_msg.edit_text(
                    "❌ Could not generate affiliate link.\n\n"
                    "This might be due to:\n"
                    "• API rate limits\n"
                    "• Invalid product\n"
                    "• Temporary server issues\n\n"
                    "Please try again in a few moments."
                )
                return
            
            # Create coin discount link
            coin_link = self.api.create_coin_discount_link(affiliate_link, product_id)
            if not coin_link:
                await processing_msg.edit_text("❌ Could not create coin discount link.")
                return
            
            # Determine link type for user info
            link_type = "🔗 Regular link"
            if 's.click.aliexpress.com' in found_url:
                link_type = "🔄 Affiliate link (converted to yours)"
            elif 'm.aliexpress.com' in found_url:
                link_type = "📱 Mobile link"
            
            # Send success message
            success_message = f"""
✅ **Coin Discount Link Generated!**

**Original:** {link_type}
**Product ID:** `{product_id}`

🔗 **Your Coin Discount Link:**
{coin_link}

💰 **Benefits:**
• Mobile coin discounts available
• Extra savings on checkout
• Optimized for mobile shopping
• **Your affiliate tracking active** 📈

🎯 **How to use:**
1. Click the link above
2. Look for coin discount options
3. Apply coins at checkout
4. Enjoy extra savings!

Happy shopping! 🛍️
            """
            
            await processing_msg.edit_text(success_message, parse_mode='Markdown')
            
        except Exception as e:
            logger.error(f"Error processing link: {e}")
            await processing_msg.edit_text(
                "❌ An error occurred while processing your link.\n\n"
                "**Possible causes:**\n"
                "• Network connectivity issues\n"
                "• API temporarily unavailable\n"
                "• Invalid or expired product link\n\n"
                "Please try again or contact support if the issue persists."
            )
    
    def run(self):
        """Start the bot"""
        logger.info("Starting AliExpress Coin Discount Bot...")
        self.app.run_polling(allowed_updates=Update.ALL_TYPES)

def main():
    # Load configuration from environment variables
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    ALIEXPRESS_APP_KEY = os.getenv("ALIEXPRESS_APP_KEY")
    ALIEXPRESS_APP_SECRET = os.getenv("ALIEXPRESS_APP_SECRET")
    ALIEXPRESS_TRACKING_ID = os.getenv("ALIEXPRESS_TRACKING_ID")
    
    # Validate configuration
    missing_vars = []
    if not BOT_TOKEN:
        missing_vars.append("BOT_TOKEN")
    if not ALIEXPRESS_APP_KEY:
        missing_vars.append("ALIEXPRESS_APP_KEY")
    if not ALIEXPRESS_APP_SECRET:
        missing_vars.append("ALIEXPRESS_APP_SECRET")
    if not ALIEXPRESS_TRACKING_ID:
        missing_vars.append("ALIEXPRESS_TRACKING_ID")
    
    if missing_vars:
        logger.error(f"Missing required environment variables: {', '.join(missing_vars)}")
        logger.error("Please set all required environment variables before running the bot.")
        return
    
    # Initialize API and bot
    api = AliExpressAPI(ALIEXPRESS_APP_KEY, ALIEXPRESS_APP_SECRET, ALIEXPRESS_TRACKING_ID)
    bot = TelegramBot(BOT_TOKEN, api)
    
    # Start bot
    bot.run()

if __name__ == "__main__":
    main()
