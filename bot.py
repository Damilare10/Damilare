import os
import re
import html
import pytz
import logging
import threading
from auth_server import app as flask_app
from pytz import timezone
from datetime import datetime, timedelta, timezone as dt_timezone
from functools import partial

# Telegram Core
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton
)
from telegram.constants import ChatType, ParseMode
from telegram.helpers import escape_markdown

# Telegram Extensions
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters, JobQueue
)

# APScheduler
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.util import astimezone
# Environment
from dotenv import load_dotenv

# Internal Database Methods
from db import (
    get_recent_approved_posts, get_user_stats, add_user, get_user, get_user_slots,
    save_post, get_pending_posts, set_post_status, deduct_slot_by_admin, expire_old_posts,
    set_twitter_handle, get_post_link_by_id, has_completed_post, mark_post_completed,
    add_task_slot, ban_unresponsive_post_owners, is_user_banned, create_verification,
    get_post_owner_id, close_verification, auto_approve_stale_posts, is_in_cooldown,
    get_user_active_posts, get_verifications_for_post, update_last_post_time,
    is_in_follow_pool, join_follow_pool, leave_follow_pool, get_follow_suggestions,
    create_follow_action, get_twitter_handle, confirm_follow_back, ignore_follow,
    count_follow_backs, count_followers
)


# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

load_dotenv()

# Configuration
API_KEY = os.getenv("TELEGRAM_TOKEN")
CHANNEL_URL = "https://t.me/Damitechinfo"
REQUIRED_GROUP = "@telemtsa"
SUPPORT_URL = "https://t.me/web3kaijun"
ADMINS = [6229232611]  # Telegram IDs of admins
GROUP_ID = -1002828603829
OAUTH_URL = "https://telegram-bot-production-d526.up.railway.app/twitter/connect"


# ──────────────────────── UTILITIES ─────────────────────────


def run_background_jobs():
    """Runs hourly jobs for expiring posts and banning unresponsive users."""
    scheduler = BackgroundScheduler(timezone=pytz.utc)

    scheduler.add_job(
        func=expire_old_posts,
        trigger="interval",
        hours=1,
        next_run_time=datetime.now(dt_timezone.utc) + timedelta(minutes=1)


    )

    scheduler.add_job(
        func=ban_unresponsive_post_owners,
        trigger="interval",
        hours=1,
        next_run_time=datetime.now(dt_timezone.utc) + timedelta(minutes=2)


    )

    scheduler.add_job(
        partial(auto_approve_stale_posts),
        "interval",
        minutes=10,
        next_run_time=datetime.now(dt_timezone.utc) + timedelta(minutes=3)
    )

    # DAILY REMINDER AT 10 AM
    scheduler.add_job(
        lambda: application.bot.send_message(
            chat_id=GROUP_ID,
            text="📢 Daily Reminder: Don’t forget to complete your raids and submit your posts!"
        ),
        trigger=CronTrigger(hour=10, minute=0, timezone='Africa/Lagos')
    )

    scheduler.start()
    logger.info("🕒 Background jobs started.")


def extract_tweet_id(url: str) -> str | None:
    """
    Extract tweet ID from a Twitter or X.com link.
    Supports both twitter.com and x.com formats.
    """
    match = re.search(r"(twitter\.com|x\.com)/\w+/status/(\d+)", url)
    if match:
        return match.group(2)
    return None

# Main menu keyboard


def is_valid_tweet_link(url: str) -> bool:
    """Check if a URL is a valid Twitter/X status link"""
    return bool(re.search(r"(twitter\.com|x\.com)/\w+/status/\d+", url))


def main_kbd(user_id: int | None = None) -> ReplyKeyboardMarkup:
    """Main keyboard layout"""
    keyboard = [
        ["🔥 Ongoing Raids"],
        ["🎯 Slots", "📤 Post", "📨 Invite Friends"],
        ["🎧 Support", "📱 Contacts", "👤 Profile"],
        ["📊 My Ongoing Raids", "🤝 Follow for Follow"]

    ]
    if user_id in ADMINS:
        keyboard.append(["🛠️ Review Posts", "📊 Stats"])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def cancel_kbd() -> ReplyKeyboardMarkup:
    """Cancel action keyboard"""
    return ReplyKeyboardMarkup([["🚫 Cancel"]], resize_keyboard=True)


def escape_markdown(text):
    return re.sub(r'([*_`\[\]])', r'\\\1', text)


async def send_daily_reminder(context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(
        chat_id=GROUP_ID,
        text="🔔 *Daily Reminder*\n\nDon't forget to complete your raids, submit your posts, and earn engagement slots today! 💰",
        parse_mode=ParseMode.MARKDOWN
    )

# ────────────────────────── COMMANDS ────────────────────────


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args
    ref_by = int(args[0]) if args and args[0].isdigit() else None

    # Enforce group join
    try:
        member = await context.bot.get_chat_member(REQUIRED_GROUP, user.id)
        if member.status not in ("member", "administrator", "creator"):
            raise Exception("Not a member")
    except:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📥 Join Beta Group",
                                  url="https://t.me/telemtsa")],
            [InlineKeyboardButton("✅ Done", callback_data="check_join")]
        ])
        await update.message.reply_text(
            "🚀 *Welcome to the Beta Test of this bot*\n\n"
            "To start using this bot, please join our *beta testing group* first.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard
        )
        return

    # Register the user
    added = add_user(user.id, user.full_name, ref_by)

    # Welcome message
    welcome = (
        f"👋 *Welcome {user.first_name} to the Web3 Raid Bot (Beta)!*\n\n"
        "Here’s what you can do:\n"
        "• 📤 Submit your Twitter/X posts for engagement (costs 1 slot)\n"
        "• ✅ Join other users' raids to earn 0.1 slots per raid\n"
        "• 📨 Invite friends to earn 0.2 slots each\n"
        "• 👤 View your profile: slot stats, referrals, and Twitter handle\n"
        "• 🧠 Manual verification system ensures fairness\n\n"
        "Beta testers get *2 free slots* and early access to all features!\n\n"
        f"🔗 Your referral link:\n`https://t.me/{context.bot.username}?start={user.id}`"
    ) if added else (
        f"*Welcome back, {user.first_name}!* 👋\n\n"
        "Here's your referral link again 🔗\n\n"
        f"`https://t.me/{context.bot.username}?start={user.id}`"
    )
    print(update.effective_chat.id)

    await update.message.reply_text(welcome, parse_mode=ParseMode.MARKDOWN)
    if update.message.chat.type == ChatType.PRIVATE:
        await update.message.reply_text("🔘 Choose an option:", reply_markup=main_kbd(user.id))


# ──────────────────────── ADMIN COMMANDS ─────────────────────


async def review_posts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin post review handler"""
    if update.effective_user.id not in ADMINS:
        await update.message.reply_text("⛔ You're not authorized.")
        return

    posts = get_pending_posts()
    if not posts:
        await update.message.reply_text("✅ No pending posts.")
        return

    for post_id, link, name, tg_id in posts:
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "✅ Approve", callback_data=f"approve|{post_id}|{tg_id}"),
            InlineKeyboardButton(
                "❌ Reject", callback_data=f"reject|{post_id}|{tg_id}")
        ]])
        await update.message.reply_text(f"👤 {name}\n🔗 {link}", reply_markup=kb)


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle admin approval/rejection"""
    query = update.callback_query
    await query.answer()

    action, post_id, user_id = query.data.split("|")
    post_id, user_id = int(post_id), int(user_id)

    if action == "approve":
        if deduct_slot_by_admin(user_id):
            set_post_status(post_id, "approved")
            await context.bot.send_message(user_id, "✅ Your post has been approved for raiding! 🚀")
            await query.edit_message_text("✅ Post approved and 1 slot deducted.")
        else:
            set_post_status(post_id, "rejected")
            await query.edit_message_text("❌ Rejected: user has no available slots.")
    else:
        set_post_status(post_id, "rejected")
        await context.bot.send_message(user_id, "❌ Your post has been rejected.")
        await query.edit_message_text("❌ Post rejected.")


async def connect_twitter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    # Keep as-is if this is used in auth_server.py
    connect_link = f"{OAUTH_URL}?telegram_id={user_id}"

    keyboard = [
        [InlineKeyboardButton("🔗 Connect Twitter", url=connect_link)]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "Click the button below to connect your Twitter account:",
        reply_markup=reply_markup
    )


# ──────────────────────── CALLBACK HANDLERS ─────────────────


async def handle_callback_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all callback button presses"""
    query = update.callback_query
    await query.answer()
    user = query.from_user
    data = query.data

    if data.startswith("confirm_twitter|"):
        handle = data.split("|")[1]
        success = set_twitter_handle(user.id, handle)

        if success:
            await query.edit_message_text(
                f"✅ Twitter handle @`{handle}` has been confirmed and saved.",
                parse_mode=ParseMode.MARKDOWN
            )
            # Go back to main menu
            await context.bot.send_message(
                chat_id=user.id,
                text="🔘 You're now connected! Choose an option:",
                reply_markup=main_kbd(user.id)
            )
            context.user_data.pop("awaiting_twitter", None)  # Clean up state
        else:
            await query.edit_message_text(
                f"❌ The handle @`{handle}` is already in use by another user.\n"
                "Please send a different Twitter handle.",
                parse_mode=ParseMode.MARKDOWN
            )
            context.user_data["awaiting_twitter"] = True

    elif data.startswith("vconfirm|"):
        _, post_id_str, doer_id_str = data.split("|")
        post_id = int(post_id_str)
        doer_id = int(doer_id_str)

        # Grant reward and close verification
        add_task_slot(doer_id, 0.1)
        close_verification(post_id, doer_id)
        await context.bot.send_message(
            chat_id=doer_id,
            text="✅ Your raid was confirmed! You've earned 0.1 slots."
        )
        await query.edit_message_text("🟢 You confirmed the raid as successful.")

    elif data.startswith("responses|"):
        await handle_view_responses(update, context)

    elif data.startswith("vreject|"):
        _, post_id_str, doer_id_str = data.split("|")
        post_id = int(post_id_str)
        doer_id = int(doer_id_str)

        close_verification(post_id, doer_id)
        await context.bot.send_message(
            chat_id=doer_id,
            text="❌ Your raid was rejected by the post owner. No slots awarded."
        )
        await query.edit_message_text("🔴 You rejected the raid.")

    elif data == "check_join":
        try:
            member = await context.bot.get_chat_member(REQUIRED_GROUP, user.id)
            if member.status in ("member", "administrator", "creator"):
                await query.edit_message_text("✅ You're in! Please click /start again to continue.")
            else:
                await query.edit_message_text("🚫 You haven't joined the group yet click /start to retry.")
        except:
            await query.edit_message_text("❌ Couldn't verify. Try again later.")

    elif data.startswith("followback|"):
        _, follower_id = data.split("|")
        follower_id = int(follower_id)
        followed_id = query.from_user.id

        confirm_follow_back(followed_id, follower_id)

        await query.answer("✅ Follow back recorded!")

        # Notify the follower
        followed_handle = get_twitter_handle(followed_id)
        followed_name = query.from_user.first_name

        await context.bot.send_message(
            chat_id=follower_id,
            text=(
                f"🎉 {followed_name} followed you back!\n\n"
                f"🔗 View their profile: https://x.com/{followed_handle}"
            )
        )

        # Confirm to the one who followed back
        await context.bot.send_message(
            chat_id=followed_id,
            text="✅ Thanks for following back!"
        )

    elif data.startswith("ignorefollow|"):
        _, follower_id = data.split("|")
        followed_id = query.from_user.id

        ignore_follow(followed_id, int(follower_id))

        # Notify the follower
        handle = get_twitter_handle(followed_id)
        x_profile_url = f"https://x.com/{handle}"

        await context.bot.send_message(
            chat_id=int(follower_id),
            text=(
                f"❌ {handle} ignored your follow request.\n\n"
                f"If you'd like, you can unfollow them here:\n\n [x.com/{handle}]({x_profile_url})"
            ),
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True
        )

        await query.answer("Ignored.")
        await query.edit_message_reply_markup(reply_markup=None)

    elif data.startswith("followdone|"):
        followed_id = int(data.split("|")[1])
        follower = query.from_user
        follower_id = follower.id

        if follower_id == followed_id:
            await query.answer("You can't follow yourself!", show_alert=True)
            return

        # Save follow action
        create_follow_action(follower_id, followed_id)

        # Notify the followed user
        handle = get_twitter_handle(follower_id)
        name = follower.username or follower.first_name
        try:
            await context.bot.send_message(
                chat_id=followed_id,
                text=(
                    f"👤 {name} says they followed you!\n\n"
                    f"🔗 X Profile: https://x.com/{handle}"
                ),
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "🔁 Follow Back", callback_data=f"followback|{follower_id}"),
                        InlineKeyboardButton(
                            "🚫 Ignore", callback_data=f"ignore_follow|{follower_id}")
                    ]
                ])
            )
        except Exception as e:
            print(f"❌ Couldn't notify user {followed_id}: {e}")

        # ✅ Edit original message to simple confirmation
        followed_user = get_user(followed_id)
        followed_name = followed_user.get("name", "this user")

        await query.edit_message_text(
            text=f"✅ You followed {followed_name}!",
        )

        await query.answer("✅ Marked as followed.")


async def handle_follow_for_follow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_data = get_user(user.id)

    if not user_data:
        await update.message.reply_text("❗ Please start the bot using /start.")
        return

    twitter_handle = user_data.get("twitter_handle")
    if not twitter_handle:
        await update.message.reply_text(
            "❗ You must set your Twitter handle before joining Follow for Follow.\n"
            "Please go to your profile to set it first."
        )
        return

    if is_in_follow_pool(user.id):
        suggestions = get_follow_suggestions(user.id)
        if not suggestions:
            await update.message.reply_text(
                "📭 No users available to follow at the moment. Try again later!"
            )
            return

        await update.message.reply_text(
            "📋 *Here are users you can follow:*\n\n"
            "✅ Follow each one and click Done under their name.",
            parse_mode=ParseMode.MARKDOWN
        )

        for target in suggestions:
            target_id = target["telegram_id"]
            target_handle = target.get("twitter_handle", "")
            target_name = target.get("name", "Unknown")

            # Get stats
            follow_count = count_followers(target_id)
            confirmed_count = count_follow_backs(target_id)

            # Escape dynamic values
            target_name_safe = escape_markdown(str(target_name))
            target_handle_safe = escape_markdown(str(target_handle))
            follow_count_safe = escape_markdown(str(follow_count))
            confirmed_count_safe = escape_markdown(str(confirmed_count))

            msg = (
                f"👤 *{target_name_safe}*\n\n"
                f"🔗 X Profile: https://x.com/{target_handle_safe}\n\n"
                f"📈 Followed by: *{follow_count_safe}* users\n"
                f"🔁 Followed back: *{confirmed_count_safe}* users\n\n"
                f"✅ Follow them and click Done below:"
            )
            await update.message.reply_text(
                msg,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=False,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "✅ Done", callback_data=f"followdone|{target_id}")
                ]])
            )

        await update.message.reply_text(
            "💡 When you're done, you can leave the pool or return to the menu:",
            reply_markup=ReplyKeyboardMarkup(
                [["🚫 Leave Pool"], ["🔙 Back to Menu"]], resize_keyboard=True
            )
        )

    else:
        context.user_data["awaiting_f4f_join"] = True
        await update.message.reply_text(
            "🤝 Join Follow for Follow pool?\n\n"
            "You'll be shown Twitter handles of others who also want to grow. "
            "Follow them and they’ll follow back!\n\n"
            "✅ Ready to join?",
            reply_markup=ReplyKeyboardMarkup(
                [["✅ Join Now"], ["🔙 Back to Menu"]], resize_keyboard=True
            )
        )


async def handle_my_ongoing_raids(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    approved_posts = get_user_active_posts(
        user.id)  # You’ll create this in db.py

    if not approved_posts:
        await update.message.reply_text("📭 You don’t have any active raids at the moment.")
        return

    for post in approved_posts:
        post_id, post_link, approved_at = post
        expires_at = datetime.fromisoformat(approved_at) + timedelta(hours=24)
        time_left = expires_at - datetime.utcnow()
        hours, minutes = divmod(int(time_left.total_seconds() // 60), 60)

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("👥 View Responses",
                                 callback_data=f"responses|{post_id}")
        ]])

        await update.message.reply_text(
            f"🧵 *Your Raid*\n🔗 {post_link}\n⏳ Time left: {hours}h {minutes}m",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard,
            disable_web_page_preview=True
        )


async def handle_raid_participation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle raid completion and ask post owner for confirmation (no API check)"""
    query = update.callback_query
    await query.answer()
    user = query.from_user
    post_id = int(query.data.split("|")[1])

    user_data = get_user(user.id)
    if not user_data:
        await query.edit_message_text("❌ You need to /start first.")
        return

    if not user_data.get("twitter_handle"):
        await query.edit_message_text("❌ You need to send your Twitter handle first.")
        return

    if has_completed_post(user.id, post_id):
        await query.edit_message_text("✅ You've already submitted this raid.")
        return

    tweet_link = get_post_link_by_id(post_id)

    if not tweet_link or not ("twitter.com" in tweet_link or "x.com" in tweet_link):
        await query.edit_message_text("❌ Invalid tweet link. It must be from Twitter or X.")
        return

    tweet_id = extract_tweet_id(tweet_link)
    if not tweet_id:
        await query.edit_message_text("❌ Unable to extract tweet ID. Make sure it's a full link.")
        return

    post_owner = get_post_owner_id(post_id)
    if not post_owner:
        await query.edit_message_text("⚠️ Could not find the post owner.")
        return

    if post_owner == user.id:
        await query.edit_message_text("❌ You cannot participate in your own raid.")
        return

    # Mark the post as completed (pending confirmation)
    mark_post_completed(user.id, post_id)

    # Create a verification entry for manual confirmation
    create_verification(post_id, user.id, post_owner)
    twitter_handle = user_data.get("twitter_handle", "N/A")
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    naija_time = datetime.now(pytz.timezone(
        "Africa/Lagos")).strftime("%Y-%m-%d %I:%M %p")
    # Notify the post owner for approval

    verifications = get_verifications_for_post(post_id)
    status = None
    for v in verifications:
        if v[0] == user.id:  # v[0] = doer_id
            status = v[3]    # v[3] = status (confirmed/rejected/pending)
            break

    # Decide buttons
    buttons = []
    if status == "pending":
        buttons = [[
            InlineKeyboardButton(
                "✅ Confirm", callback_data=f"vconfirm|{post_id}|{user.id}"),
            InlineKeyboardButton(
                "❌ Reject", callback_data=f"vreject|{post_id}|{user.id}")
        ]]

    await context.bot.send_message(
        chat_id=post_owner,
        text=(
            f"📣 {user.username or user.full_name} says they've completed your raid:\n"
            f"🔗 {tweet_link}\n"
            f"🐦 Twitter: @{twitter_handle}\n\n"
            f"🕒 Submitted: {timestamp}\n\n"
            f"🕒 Submitted: {naija_time} (Nigerian Time)\n\n"
            f"{'Do you confirm this?' if buttons else '✅ Already reviewed.'}"
        ),
        reply_markup=InlineKeyboardMarkup(buttons) if buttons else None
    )

    await query.edit_message_text("✅ Raid submitted. Waiting for the post owner to confirm.")


async def handle_view_responses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    post_id = int(query.data.split("|")[1])
    verifications = get_verifications_for_post(post_id)  # define this in db.py

    if not verifications:
        await query.edit_message_text("📭 No responses for this raid yet.")
        return

    for v in verifications:
        doer_id, raider_username, raider_handle, status = v
        name = f"{raider_username}\n\n" if raider_username else f"User {doer_id}"
        handle = f"X: (@{raider_handle})" if raider_handle else ""
        label = f"{name} {handle} — Status: {status or 'Pending'}"

        # Only show buttons if still pending
        if status == "pending":
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "✅ Confirm", callback_data=f"vconfirm|{post_id}|{doer_id}"),
                InlineKeyboardButton(
                    "❌ Reject", callback_data=f"vreject|{post_id}|{doer_id}")
            ]])
        else:
            keyboard = None  # No buttons for confirmed/rejected

        await query.message.reply_text(label, reply_markup=keyboard)


# ────────────────────────── MESSAGE HANDLERS ─────────────────


async def handle_message_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all text messages"""
    txt = update.message.text.strip()
    user = update.effective_user

    if txt == "🔥 Ongoing Raids":
        await handle_ongoing_raids(update, context)

    elif txt == "🤝 Follow for Follow":
        await handle_follow_for_follow(update, context)

    elif txt == "✅ Join Now":
        user_data = get_user(user.id)
        if not user_data or not user_data.get("twitter_handle"):
            await update.message.reply_text(
                "❗ You must connect your Twitter account before joining Follow for Follow.\n\n"
                "🔗 Tap below to connect:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "🔗 Connect Twitter", url=f"{OAUTH_URL}?telegram_id={user.id}")]
                ])
            )
            return

        join_follow_pool(user.id, user_data["twitter_handle"])
        context.user_data["awaiting_f4f_join"] = False
        await update.message.reply_text(
            "🎉 You’ve joined the Follow for Follow pool!",
            reply_markup=main_kbd(user.id)
        )

    elif txt == "🚫 Leave Pool":
        leave_follow_pool(user.id)
        await update.message.reply_text(
            "❌ You’ve left the Follow for Follow pool.",
            reply_markup=main_kbd(user.id)
        )

    elif txt == "🔙 Back to Menu":
        await update.message.reply_text("🔙 Back to main menu.", reply_markup=main_kbd(user.id))

    elif txt == "🎯 Slots":
        await handle_slots(update, context)

    elif txt == "📤 Post":
        context.user_data["awaiting_post"] = True
        await update.message.reply_text(
            "📤 *Submit your Twitter/X post link for review:*\n\n"
            "🔗 Please paste a *valid Twitter (twitter.com) or X (x.com) post link* below.\n"
            "Example: https://x.com/Web3Kaiju/status/1901622919777652813",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=cancel_kbd()
        )

    elif txt == "📨 Invite Friends":
        await handle_referrals(update, context)

    elif txt == "🎧 Support":
        await handle_support(update, context)

    elif txt == "📱 Contacts":
        await handle_contacts(update, context)

    elif txt == "🛠️ Review Posts":
        await review_posts(update, context)

    elif txt == "🚫 Cancel":
        await handle_cancel(update, context)

    elif context.user_data.get("awaiting_post"):
        await handle_post_submission(update, context)

    elif txt == "👤 Profile":
        await handle_profile(update, context)

    elif txt == "📊 Stats":
        await handle_stats_backup(update, context)

    elif txt == "📊 My Ongoing Raids":
        await handle_my_ongoing_raids(update, context)

    elif txt == "📥 Pending Followers":
        pending = get_pending_followers(user.id)
        if not pending:
            await update.message.reply_text("📭 No one has followed you recently.")
        else:
            for follower_id, name, handle in pending:
                await update.message.reply_text(
                    f"👤 @{handle or name} followed you.\nClick below to respond.",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton(
                            "🔁 Follow Back", callback_data=f"followback|{follower_id}"),
                        InlineKeyboardButton(
                            "🚫 Ignore", callback_data=f"ignorefollow|{follower_id}")
                    ]])
                )

    else:
        context.user_data["awaiting_post"] = False  # optional cleanup
        await update.message.reply_text(
            "❓ I didn't understand that. Choose an option:",
            reply_markup=main_kbd(user.id)
        )


# ──────────────────────── HANDLER HELPERS ────────────────────

async def handle_ongoing_raids(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle ongoing raids display"""
    user = update.effective_user
    chat = update.effective_chat
    user_data = get_user(user.id)

    if not user_data:
        username = html.escape(user.username or user.first_name)
        await update.message.reply_text(
            f"👋 <b>@{username}</b>, please start the bot in private:<br>"
            f"<a href='https://t.me/{context.bot.username}?start={user.id}'>Click here</a>",
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )
        return

    # If user hasn't set Twitter handle
    if not user_data.get("twitter_handle"):
        if chat.type != "private":
            username = html.escape(user.username or user.first_name)
            await update.message.reply_text(
                f"❗️<b>@{username}</b>, to join raids, please message the bot privately first:<br>"
                f"👉 <a href='https://t.me/{context.bot.username}?start={user.id}'>Click here to set your Twitter handle</a><br><br>"
                f"Then tap <b>🔥 Ongoing Raids</b> to continue.",
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True
            )
            return
        else:
            await update.message.reply_text(
                "🐦 To join raids, please connect your Twitter account first:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "🔗 Connect Twitter", url=f"{OAUTH_URL}?telegram_id={user.id}")]
                ])
            )
            return

    # Continue with showing raids
    group_id = chat.id if chat.type in ("group", "supergroup") else None
    posts = get_recent_approved_posts(group_id=group_id, with_time=True)

    if not posts:
        await update.message.reply_text("🚫 No active raids in the last 24 hours.")
    else:
        for post_id, post_link, name, approved_at_str in posts:
            try:
                approved_at = datetime.fromisoformat(approved_at_str)
                if approved_at.tzinfo is None:
                    approved_at = approved_at.replace(tzinfo=dt_timezone.utc)
            except Exception:
                await update.message.reply_text(f"⚠️ Skipping a post due to time error: {approved_at_str}")
                continue

            expires_at = approved_at + timedelta(hours=24)
            now = datetime.now(dt_timezone.utc)
            time_left = expires_at - now

            if time_left.total_seconds() <= 0:
                continue  # Skip expired

            hours_left = int(time_left.total_seconds() // 3600)
            minutes_left = int((time_left.total_seconds() % 3600) // 60)
            time_left_str = f"{hours_left}h {minutes_left}m left"

            if has_completed_post(user.id, post_id):
                status = "✅ You’ve already joined this raid."
                keyboard = None
            else:
                status = "❌ You haven’t joined this raid yet."
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "✅ Done", callback_data=f"done|{post_id}")]
                ])

            escaped_name = html.escape(name)
            escaped_link = html.escape(post_link)

            await update.message.reply_text(
                f"🔥 <b>New Raid by {name}</b>\n\n"
                f"🔗 <a href=\"{post_link}\">{post_link}</a>\n\n"
                f"{status}\n🕒 <b>Time Left:</b> {time_left_str}",
                reply_markup=keyboard,
                parse_mode="HTML",
                disable_web_page_preview=True
            )


async def handle_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle profile display"""
    user = update.effective_user
    user_data = get_user(user.id)

    if not user_data:
        await update.message.reply_text("❗️User not found. Please start the bot using /start.")
        return

    stats = get_user_stats(user.id)
    approved, rejected, task_slots, ref_slots = stats

    twitter = user_data.get("twitter_handle")
    twitter_display = f"@{escape_markdown(twitter)}" if twitter else "❌ Not connected"

    await update.message.reply_text(
        f"👤 *Your Profile*\n\n"
        f"🐦 Twitter: {twitter_display}\n\n"
        f"✅ Approved Posts: {approved}\n"
        f"❌ Rejected Posts: {rejected}\n\n"
        f"💰 Slot Earnings:\n"
        f"🪙 From Raids: {task_slots}\n"
        f"👥 From Referrals: {ref_slots}",
        parse_mode=ParseMode.MARKDOWN
    )

    if not twitter:
        await update.message.reply_text(
            "🔗 You haven't connected your Twitter account yet.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "Connect Twitter", url=f"{OAUTH_URL}?telegram_id={user.id}")
            ]])
        )


async def handle_slots(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle slots display"""
    user = update.effective_user
    slots = get_user_slots(user.id)
    await update.message.reply_text(
        f"🎯 *Slot Info*\n\nHi {user.first_name}, you have *{slots}* engagement slot(s).\n\n"
        "📌 Earn more slots by participating in raids or referring others!",
        parse_mode=ParseMode.MARKDOWN
    )


async def handle_post_submission(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle post submission"""
    user = update.effective_user
    user_data = get_user(user.id)

    # 🔒 Check if user is banned from posting
    if is_user_banned(user.id):
        await update.message.reply_text(
            "⛔ You are temporarily banned from posting due to unverified raids.\n"
            "📆 You can post again after 48 hours.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # 📨 User is submitting a tweet link
    text = update.message.text.strip()

    # 🔗 Validate tweet link
    if not is_valid_tweet_link(text):
        await update.message.reply_text(
            "❌ Invalid tweet link. Only links from *twitter.com* or *x.com* are allowed.\n"
            "Please send a valid Twitter/X post link:",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # ⏳ Check 12-hour cooldown
    cooldown_hours = 12
    in_cooldown, remaining = is_in_cooldown(user.id, cooldown_hours)
    if in_cooldown:
        await update.message.reply_text(
            f"⏳ You can only submit one post every {cooldown_hours} hours.\n"
            f"🕒 Please wait {remaining} more before submitting again."
        )
        return

    # 💾 Save the post
    chat = update.effective_chat
    group_id = chat.id if chat.type in ("group", "supergroup") else None
    print("✅ About to save post")
    save_post(user.id, text, group_id=group_id)
    print("✅ Post saved")
    update_last_post_time(user.id)
    context.user_data["awaiting_post"] = False

    # ✅ Notify user
    await update.message.reply_text(
        "✅ Your post has been submitted for review. You'll be notified when it's approved.",
        reply_markup=main_kbd(user.id),
    )

    # 📢 Notify admins
    name = user.full_name
    for admin_id in ADMINS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=f"📬 New post submitted by *{name}*:\n{text}",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            print(f"[ADMIN NOTIFY ERROR] Admin ID: {admin_id} - {e}")


async def post_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["awaiting_post"] = True
    await update.message.reply_text("📨 Please send the Twitter/X post link you'd like to submit.")

    # First-time call to /post or menu button
    await update.message.reply_text(
        "📤 *Submit your Twitter/X post link for review:*\n\n"
        "🔗 Please paste a *valid Twitter (twitter.com) or X (x.com) post link* below.\n"
        "Example: https://x.com/Web3Kaiju/status/1901622919777652813",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=cancel_kbd()
    )
    context.user_data["awaiting_post"] = True


async def handle_stats_backup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send raw DB file to admin."""
    user = update.effective_user
    if user.id not in ADMINS:
        return

    db_path = "bot_data.db"  # or your actual DB file path

    if not os.path.exists(db_path):
        await update.message.reply_text("❌ Database file not found.")
        return

    await update.message.reply_document(
        document=open(db_path, "rb"),
        filename="bot_data_backup.db",
        caption="📦 Here is the current bot_data.db backup.\nYou can restore it after redeploying.",
    )


async def handle_referrals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle referral program"""
    user = update.effective_user
    user_data = get_user(user.id)
    if not user_data:
        await update.message.reply_text("❗ You need to start the bot with /start first.")
        return

    ref_link = f"https://t.me/{context.bot.username}?start={user.id}"
    ref1 = user_data["ref_count_l1"] if user_data else 0

    await update.message.reply_text(
        "📨 *Referral Program*\n\n"
        "🎯 Invite others and earn *0.2 engagement slot* per referral!\n\n"
        f"🔗 Your referral link:\n`{ref_link}`\n\n"
        f"📊 *Total Referrals:* {ref1}",
        parse_mode=ParseMode.MARKDOWN
    )


async def handle_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle support request"""
    await update.message.reply_text(
        "🎧 *Need help with the Bot?*\n\n"
        "Tap the button below to chat with us:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("Contact Us", url=SUPPORT_URL)]]
        )
    )


async def handle_contacts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle contact information"""
    await update.message.reply_text(
        "📩 *Contact Us:*\n\n"
        "📧 web3kaiju@gmail.com\n"
        "🔗 X: https://x.com/web3kaiju\n"
        "📱 Telegram: https://t.me/web3kaijun\n"
        "📞 WhatsApp: https://wa.me/+2347043031993",
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True
    )


async def has_joined_required_group(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        member = await context.bot.get_chat_member(REQUIRED_GROUP, user_id)
        return member.status in ("member", "administrator", "creator")
    except:
        return False


async def verify_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if not data.startswith("v|"):
        return

    _, post_id_str = data.split("|")
    post_id = int(post_id_str)
    telegram_id = query.from_user.id

    # Check if user already completed this post
    if has_completed_post(telegram_id, post_id):
        await query.edit_message_text("❗️You've already completed this raid.")
        return

    # Get post info
    post = get_post(post_id)
    if not post:
        await query.edit_message_text("❗️This post no longer exists.")
        return

    tweet_url = post[2]
    tweet_id = tweet_url.split("/")[-1]

    # Get user token from DB
    user = get_user(telegram_id)
    access_token = user.get("access_token")

    if not access_token:
        await query.edit_message_text("❗️Your Twitter account is not connected.")
        return


async def handle_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle cancel action"""
    context.user_data.pop("awaiting_post", None)
    await update.message.reply_text("Back to main menu.", reply_markup=main_kbd(update.effective_user.id))


def run_flask():
    flask_app.run(host="0.0.0.0", port=8080)

# ─────────────────────────── MAIN ────────────────────────────


def main():
    """Start the bot"""

    # Set timezone using pytz and convert with astimezone (required by APScheduler)
    lagos_tz = pytz.timezone("Africa/Lagos")

    # Build the app first — don't pass job_queue manually
    app = ApplicationBuilder().token(API_KEY).build()

    # Configure the job queue scheduler explicitly
    app.job_queue.scheduler.configure(timezone=astimezone(lagos_tz))

    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()

    # Run background tasks
    run_background_jobs()

    # ─────────────── HANDLERS ───────────────
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("profile", handle_profile))
    app.add_handler(CommandHandler("slots", handle_slots))
    app.add_handler(CommandHandler("review", review_posts))
    app.add_handler(CommandHandler("post", handle_post_submission))
    app.add_handler(CommandHandler("referrals", handle_referrals))
    app.add_handler(CommandHandler("support", handle_support))
    app.add_handler(CommandHandler("contacts", handle_contacts))
    app.add_handler(CommandHandler("connect", connect_twitter))
    app.add_handler(CommandHandler("ongoing_raids", handle_ongoing_raids))
    app.add_handler(CommandHandler("my_raids", handle_my_ongoing_raids))

    app.add_handler(CallbackQueryHandler(
        handle_callback_buttons, pattern=r"^(confirm_twitter|responses|vconfirm|vreject)\|"))
    app.add_handler(CallbackQueryHandler(
        handle_raid_participation, pattern=r"^done\|"))
    app.add_handler(CallbackQueryHandler(
        admin_callback, pattern=r"^(approve|reject)\|"))
    app.add_handler(CallbackQueryHandler(handle_callback_buttons))

    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
        handle_message_buttons
    ))

    logger.info("🤖 Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
