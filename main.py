import os
import re
import requests
import hashlib
import json
import time
from urllib.parse import urlparse, parse_qs, urlencode

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
import logging

# --- Configuration ---
# Set up logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Load environment variables
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALIEXPRESS_APP_KEY = os.getenv("ALIEXPRESS_APP_KEY")
ALIEXPRESS_APP_SECRET = os.getenv("ALIEXPRESS_APP_SECRET")
ALIEXPRESS_TRACKING_ID = os.getenv("ALIEXPRESS_TRACKING_ID")

if not all([TELEGRAM_BOT_TOKEN, ALIEXPRESS_APP_KEY, ALIEXPRESS_APP_SECRET, ALIEXPRESS_TRACKING_ID]):
    logger.error("Missing one or more environment variables. Please check your .env file.")
    exit(1)

# --- AliExpress API Class (from your first bot, with minor adjustments) ---
class AliExpressAPI:
    def __init__(self, app_key, app_secret, tracking_id):
        self.app_key = app_key
        self.app_secret = app_secret
        self.tracking_id = tracking_id
        self.api_url = "https://api-sg.aliexpress.com/sync"
        self.short_url_pattern = re.compile(r"https?://s\.click\.aliexpress\.com/e/[^/\s]+")
        self.product_id_patterns = [
            re.compile(r"aliexpress\.com/item/(\d+)\.html"),
            re.compile(r"aliexpress\.com/i/(\d+)\.html"),
            re.compile(r"aliexpress\.com/item/\d+\.html\?.*id=(\d+)") # For URLs with ?id= in query
        ]

    def generate_signature(self, params):
        """Generates the MD5 signature for AliExpress API requests."""
        sorted_params = sorted(params.items())
        sign_string = self.app_secret
        for k, v in sorted_params:
            if v is not None:  # Ensure no None values in signature string
                sign_string += k + str(v)
        sign_string += self.app_secret
        return hashlib.md5(sign_string.encode("utf-8")).hexdigest().upper()

    async def resolve_short_url(self, short_url):
        """Resolves a short AliExpress URL to its final destination."""
        try:
            # Use HEAD request to avoid downloading content, just get redirects
            response = requests.head(short_url, allow_redirects=True, timeout=10)
            response.raise_for_status()  # Raise an exception for bad status codes
            return response.url
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to resolve short URL {short_url}: {e}")
            return None

    def extract_product_id(self, url):
        """Extracts the product ID from a given AliExpress URL."""
        # First, try to resolve if it's a short URL
        resolved_url = url
        if self.short_url_pattern.match(url):
            resolved_url = requests.head(url, allow_redirects=True, timeout=10).url
            if not resolved_url:
                logger.warning(f"Could not resolve short URL to get product ID: {url}")
                return None

        for pattern in self.product_id_patterns:
            match = pattern.search(resolved_url)
            if match:
                return match.group(1)
        logger.warning(f"Could not extract product ID from URL: {resolved_url}")
        return None

    async def get_affiliate_link(self, product_id):
        """Generates an affiliate link for a given product ID."""
        if not product_id:
            logger.warning("Product ID is missing, cannot generate affiliate link.")
            return None

        clean_product_url = f"https://www.aliexpress.com/item/{product_id}.html"

        params = {
            "app_key": self.app_key,
            "format": "json",
            "method": "aliexpress.affiliate.link.generate",
            "promotion_link_type": "2",  # '2' for mobile/app (recommended for coin deals)
            "sign_method": "md5",
            "timestamp": str(int(time.time() * 1000)),
            "source_values": clean_product_url,
            "tracking_id": self.tracking_id,
        }
        params["sign"] = self.generate_signature(params)

        try:
            response = requests.post(self.api_url, data=params, timeout=10)
            response.raise_for_status()
            result = response.json()

            logger.info(f"AliExpress affiliate link API response: {json.dumps(result, indent=2)}")

            if "aliexpress_affiliate_link_generate_response" in result:
                resp_result = result["aliexpress_affiliate_link_generate_response"].get("resp_result")
                if resp_result and resp_result.get("resp_code") == "200":
                    promotion_links = resp_result.get("promotion_links")
                    if promotion_links:
                        # Find the link that opens in the app, usually contains "aff_platform=msite" or similar
                        for link_obj in promotion_links:
                            if "promotion_link" in link_obj:
                                # We assume the main affiliate link is the one we want
                                # If you need a specific type (e.g., app link), you might check for specific query params in link_obj['promotion_link']
                                return link_obj["promotion_link"]
                else:
                    error_msg = resp_result.get("resp_msg", "Unknown error") if resp_result else "No resp_result"
                    logger.error(f"AliExpress API returned an error: {error_msg}")
            elif "error_response" in result:
                error_details = result["error_response"]
                logger.error(f"AliExpress API error: Code={error_details.get('code')}, Msg={error_details.get('msg')}, SubCode={error_details.get('sub_code')}")
            else:
                logger.error(f"Unexpected AliExpress API response format: {json.dumps(result)}")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"AliExpress API request failed: {e}")
            return None

    async def get_product_details(self, product_id):
        """Fetches product details for a given product ID."""
        if not product_id:
            logger.warning("Product ID is missing, cannot fetch product details.")
            return None

        params = {
            "app_key": self.app_key,
            "format": "json",
            "method": "aliexpress.affiliate.productdetail.get",
            "product_ids": product_id,
            "sign_method": "md5",
            "timestamp": str(int(time.time() * 1000)),
            "target_currency": "USD",
            "target_language": "en", # Keep English for API response consistency
        }
        params["sign"] = self.generate_signature(params)

        try:
            response = requests.post(self.api_url, data=params, timeout=10)
            response.raise_for_status()
            result = response.json()

            logger.info(f"AliExpress product details API response: {json.dumps(result, indent=2)}")

            if "aliexpress_affiliate_productdetail_get_response" in result:
                resp_result = result["aliexpress_affiliate_productdetail_get_response"].get("resp_result")
                if resp_result and resp_result.get("resp_code") == "200":
                    product_infos = resp_result.get("product_infos")
                    if product_infos:
                        return product_infos[0] # Return the first product info object
                else:
                    error_msg = resp_result.get("resp_msg", "Unknown error") if resp_result else "No resp_result"
                    logger.warning(f"AliExpress Product Details API returned an error: {error_msg}")
            elif "error_response" in result:
                error_details = result["error_response"]
                logger.warning(f"AliExpress Product Details API error: Code={error_details.get('code')}, Msg={error_details.get('msg')}")
            else:
                logger.warning(f"Unexpected AliExpress Product Details API response format: {json.dumps(result)}")
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"AliExpress Product Details API request failed: {e}")
            return None

    def create_coin_discount_link(self, affiliate_link, product_id):
        """
        Creates a mobile-specific coin discount link from an affiliate link.
        This relies on the structure of AliExpress affiliate links, which might change.
        """
        try:
            parsed_aff_link = urlparse(affiliate_link)
            aff_params = parse_qs(parsed_aff_link.query)

            # Extract necessary parameters for the coin link
            aff_fcid = aff_params.get("aff_fcid", [""])[0]
            aff_fsk = aff_params.get("aff_fsk", [""])[0]
            aff_trace_key = aff_params.get("aff_trace_key", [""])[0]
            terminal_id = aff_params.get("terminal_id", [""])[0] # Often '5050', '2' or '4'

            base_coin_url = "https://a.aliexpress.com/_coin-index"
            
            # Use a dictionary to build parameters for cleaner URL encoding
            coin_params = {
                "productId": product_id,
                "aff_fcid": aff_fcid,
                "aff_fsk": aff_fsk,
                "aff_trace_key": aff_trace_key,
                "terminal_id": terminal_id,
            }
            
            # Filter out empty parameters before encoding
            filtered_coin_params = {k: v for k, v in coin_params.items() if v}

            return f"{base_coin_url}?{urlencode(filtered_coin_params)}"
        except Exception as e:
            logger.error(f"Failed to create coin discount link from {affiliate_link}: {e}")
            return None

# --- Telegram Bot Handlers ---
api = AliExpressAPI(ALIEXPRESS_APP_KEY, ALIEXPRESS_APP_SECRET, ALIEXPRESS_TRACKING_ID)

# Inline keyboard setup (simplified)
keyboard_main = InlineKeyboardMarkup(
    [[InlineKeyboardButton("❤️ اشترك في القناة للمزيد من العروض ❤️", url="t.me/Tcoupon")]]
)

@logger.catch
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a welcome message with instructions."""
    await update.message.reply_text(
        "مرحبا بك، ارسل لنا رابط المنتج الذي تريد شرائه لنوفر لك افضل سعر له 👌 \n"
        "يرجى إرسال <b>الرابط فقط</b>، بدون عنوان المنتج.",
        reply_markup=keyboard_main,
        parse_mode='HTML'
    )

@logger.catch
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Processes user messages, extracts links, and generates discount links."""
    text = update.message.text
    chat_id = update.message.chat_id

    # Regex to find any URL in the message
    url_match = re.search(r"https?://\S+|www\.\S+", text)
    found_url = url_match.group(0) if url_match else None

    if not found_url or "aliexpress.com" not in found_url:
        await update.message.reply_text(
            "الرابط غير صحيح ! تأكد من رابط المنتج أو اعد المحاولة.\n"
            "قم بإرسال <b> الرابط فقط</b> بدون عنوان المنتج",
            parse_mode='HTML'
        )
        return

    # Send a "Processing" message and get its ID to edit later
    processing_msg = await update.message.reply_text("🔄 يتم معالجة الرابط الخاص بك...")

    try:
        # Step 1: Extract Product ID
        product_id = api.extract_product_id(found_url)
        if not product_id:
            await processing_msg.edit_text(
                "❌ تعذر استخراج معرف المنتج من الرابط. يرجى التأكد من أنه رابط منتج صحيح."
            )
            return

        # Step 2: Get your affiliate link
        affiliate_link = await api.get_affiliate_link(product_id)
        if not affiliate_link:
            await processing_msg.edit_text(
                "❌ تعذر إنشاء رابط العمولات الخاص بك.\n\n"
                "قد يكون هذا بسبب:\n"
                "• تجاوز حدود استخدام واجهة برمجة التطبيقات (API rate limits)\n"
                "• منتج غير صالح\n"
                "• مشاكل مؤقتة في الخادم\n\n"
                "الرجاء المحاولة مرة أخرى في بضع لحظات."
            )
            return

        # Step 3: Create the Coin Discount Link
        coin_discount_link = api.create_coin_discount_link(affiliate_link, product_id)
        if not coin_discount_link:
            await processing_msg.edit_text(
                "❌ تعذر إنشاء رابط خصم العملات.\n\n"
                "حدث خطأ أثناء تحويل رابط العمولات إلى رابط خصم العملات."
            )
            return

        # Step 4: Get product details for a richer response
        product_details = await api.get_product_details(product_id)

        response_text = (
            f"✅ تم تحويل الرابط بنجاح!\n\n"
            f"🔗 رابط خصم العملات الخاص بك:\n"
            f"<code>{coin_discount_link}</code>\n\n"
            f"استخدم هذا الرابط في تطبيق AliExpress للحصول على أفضل سعر وخصم بالعملات المعدنية!"
            f"\n\nLa Deals !"
        )
        
        photo_url = None
        if product_details:
            title = product_details.get('product_title', 'عنوان المنتج غير متوفر')
            price = product_details.get('target_sale_price', 'السعر غير متوفر')
            photo_url = product_details.get('product_main_image_url')

            response_text = (
                f"✅ تم تحويل الرابط بنجاح!\n\n"
                f"🛒 منتجك هو : 🔥 \n{title} 🛍 \n"
                f"سعر المنتج : {price} دولار 💵\n\n"
                f"🔗 رابط خصم العملات الخاص بك:\n"
                f"<code>{coin_discount_link}</code>\n\n"
                f"استخدم هذا الرابط في تطبيق AliExpress للحصول على أفضل سعر وخصم بالعملات المعدنية!"
                f"\n\nLa Deals !"
            )
        
        if photo_url:
            await context.bot.delete_message(chat_id=chat_id, message_id=processing_msg.message_id)
            await update.message.reply_photo(
                photo=photo_url,
                caption=response_text,
                reply_markup=keyboard_main,
                parse_mode='HTML'
            )
        else:
            await processing_msg.edit_text(
                response_text,
                reply_markup=keyboard_main,
                parse_mode='HTML'
            )

    except Exception as e:
        logger.exception("An unhandled error occurred in handle_message:")
        await processing_msg.edit_text(
            "❌ حدث خطأ غير متوقع أثناء معالجة طلبك. يرجى المحاولة مرة أخرى لاحقًا."
        )

def main():
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot started polling...")
    application.run_polling(poll_interval=1, timeout=30, read_timeout=10, write_timeout=10) # Adjust timeouts as needed

if __name__ == "__main__":
    # Ensure all required environment variables are set before running
    # You'll need to set these in your environment or a .env file (and load it)
    # Example (if using python-dotenv, which you should for local development):
    # from dotenv import load_dotenv
    # load_dotenv()
    main()
