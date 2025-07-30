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
        sorted_params = sorted(params.items())
        
        query_string = api_name
        for key, value in sorted_params:
            query_string += f"{key}{value}"
        query_string += self.app_secret
        
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
        # Extract product ID from URL
        product_id = self.extract_product_id(product_url)
        if not product_id:
            return None
        
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
            "source_values": product_url,
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
    
    def extract_product_id(self, url):
        """Extract product ID from AliExpress URL"""
        patterns = [
            r'/item/(\d+)\.html',
            r'productIds=(\d+)',
            r'/(\d+)\.html',
            r'aliexpress\.com/item/([^/]+)',
            r'product/(\d+)'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        
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

1. **Find a product** on AliExpress
2. **Copy the link** (any AliExpress product URL)
3. **Send it here** - just paste and send
4. **Get your coin discount link** instantly!

**Supported link formats:**
• https://www.aliexpress.com/item/...
• https://aliexpress.com/item/...
• https://m.aliexpress.com/item/...
• Short AliExpress links (s.click.aliexpress.com)

**Example:**
Send: `https://www.aliexpress.com/item/1005004633663909.html`
Get: Mobile coin discount link with extra savings! 💰

**Need help?** Just send /help anytime!
        """
        await update.message.reply_text(help_message, parse_mode='Markdown')
    
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle incoming messages with URLs"""
        message_text = update.message.text
        
        aliexpress_patterns = [
            r'https?://(?:www\.)?aliexpress\.com/item/\d+\.html',
            r'https?://(?:www\.)?aliexpress\.com/item/[^/]+',
            r'https?://m\.aliexpress\.com/item/\d+\.html',
            r'https?://s\.click\.aliexpress\.com/e/[^/\s]+',
            r'https?://(?:www\.)?aliexpress\.com/[^\s]*'
        ]
        
        found_url = None
        for pattern in aliexpress_patterns:
            match = re.search(pattern, message_text)
            if match:
                found_url = match.group(0)
                break
        
        if not found_url:
            await update.message.reply_text(
                "❌ No AliExpress link found!\n\n"
                "Please send a valid AliExpress product link.\n"
                "Example: https://www.aliexpress.com/item/1005004633663909.html"
            )
            return
        
        processing_msg = await update.message.reply_text("🔄 Processing your link...")
        
        try:
            product_id = self.api.extract_product_id(found_url)
            if not product_id:
                await processing_msg.edit_text("❌ Could not extract product ID from the link.")
                return
            
            affiliate_link = self.api.get_affiliate_link(found_url, promotion_link_type="2")
            if not affiliate_link:
                await processing_msg.edit_text("❌ Could not generate affiliate link. Please try again later.")
                return
            
            coin_link = self.api.create_coin_discount_link(affiliate_link, product_id)
            if not coin_link:
                await processing_msg.edit_text("❌ Could not create coin discount link.")
                return
            
            success_message = f"""
✅ **Coin Discount Link Generated!**

🔗 **Your Link:**
{coin_link}

💰 **Benefits:**
• Mobile coin discounts available
• Extra savings on checkout
• Optimized for mobile shopping

**Product ID:** `{product_id}`

Enjoy your savings! 🛍️
            """
            
            await processing_msg.edit_text(success_message, parse_mode='Markdown')
            
        except Exception as e:
            logger.error(f"Error processing link: {e}")
            await processing_msg.edit_text("❌ An error occurred while processing your link. Please try again.")
    
    def run(self):
        """Start the bot"""
        logger.info("Starting AliExpress Coin Discount Bot...")
        self.app.run_polling(allowed_updates=Update.ALL_TYPES)

def main():
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    ALIEXPRESS_APP_KEY = os.getenv("ALIEXPRESS_APP_KEY")
    ALIEXPRESS_APP_SECRET = os.getenv("ALIEXPRESS_APP_SECRET")
    ALIEXPRESS_TRACKING_ID = os.getenv("ALIEXPRESS_TRACKING_ID")
    
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
    
    api = AliExpressAPI(ALIEXPRESS_APP_KEY, ALIEXPRESS_APP_SECRET, ALIEXPRESS_TRACKING_ID)
    bot = TelegramBot(BOT_TOKEN, api)
    
    bot.run()

if __name__ == "__main__":
    main()
