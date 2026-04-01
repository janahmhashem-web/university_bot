# ... (تابع الملف من هنا)

async def get_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not sheets_client:
            await update.message.reply_text("⚠️ النظام غير متصل بقاعدة البيانات حالياً.")
            return
        if context.args:
            transaction_id = context.args[0]
            logger.info(f"🔍 البحث عن ID: {transaction_id}")
            data = sheets_client.get_latest_row_by_id_fast(Config.SHEET_MANAGER, transaction_id)
            if data:
                msg = f"🔍 *تفاصيل المعاملة {transaction_id}:*\n"
                for key in ['اسم صاحب المعاملة الثلاثي', 'الحالة', 'الموظف المسؤول']:
                    if key in data and data[key]:
                        msg += f"• {key}: {data[key]}\n"
                base_url = request.host_url.rstrip('/')
                msg += f"\n🔗 [رابط المتابعة]({base_url}/view/{transaction_id})"
                await update.message.reply_text(msg, parse_mode='Markdown', disable_web_page_preview=True)
            else:
                await update.message.reply_text(f"❌ لا توجد معاملة بالرقم {transaction_id}")
        else:
            await update.message.reply_text("الرجاء إدخال رقم المعاملة: /id 123")
    except Exception as e:
        logger.error(f"❌ خطأ في get_id: {e}", exc_info=True)
        await update.message.reply_text("عذراً، حدث خطأ.")

async def get_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not sheets_client:
            await update.message.reply_text("⚠️ النظام غير متصل بقاعدة البيانات.")
            return
        if context.args:
            transaction_id = context.args[0]
            ws = sheets_client.get_worksheet(Config.SHEET_HISTORY)
            if not ws:
                await update.message.reply_text("❌ لا يوجد سجل تاريخ.")
                return
            records = ws.get_all_records()
            history = [r for r in records if str(r.get('ID')) == transaction_id]
            if history:
                history.sort(key=lambda x: x.get('timestamp', ''))
                msg = f"📜 *سجل تتبع المعاملة {transaction_id}:*\n"
                for entry in history:
                    time_str = entry.get('timestamp', '')
                    action = entry.get('action', '')
                    user = entry.get('user', '')
                    msg += f"• {time_str} - {action} (بواسطة: {user})\n"
                await update.message.reply_text(msg, parse_mode='Markdown')
            else:
                await update.message.reply_text(f"لا يوجد سجل للمعاملة {transaction_id}")
        else:
            await update.message.reply_text("الرجاء إدخال رقم المعاملة: /history 123")
    except Exception as e:
        logger.error(f"خطأ في history: {e}")
        await update.message.reply_text("حدث خطأ.")

async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not sheets_client:
            await update.message.reply_text("⚠️ النظام غير متصل بقاعدة البيانات.")
            return
        if context.args:
            keyword = ' '.join(context.args)
            records = sheets_client.get_latest_transactions_fast(Config.SHEET_MANAGER)
            found = []
            for r in records:
                if keyword in str(r.values()):
                    found.append(r.get('ID', ''))
            if found:
                await update.message.reply_text(f"🔎 المعاملات التي تحتوي على '{keyword}':\n" + "\n".join(found[:10]))
            else:
                await update.message.reply_text("لا توجد نتائج.")
        else:
            await update.message.reply_text("الرجاء إدخال كلمة للبحث: /search كلمة")
    except Exception as e:
        logger.error(f"خطأ في search: {e}")
        await update.message.reply_text("حدث خطأ.")

async def wake(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ البوت نشط وجاهز!")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        if user_id != Config.ADMIN_CHAT_ID:
            await update.message.reply_text("⛔ هذا الأمر متاح فقط للمدير.")
            return
        if not sheets_client:
            await update.message.reply_text("⚠️ غير متصل بقاعدة البيانات.")
            return
        records = sheets_client.get_latest_transactions_fast(Config.SHEET_MANAGER)
        total = len(records)
        completed = sum(1 for r in records if r.get('الحالة') == 'مكتملة')
        pending = sum(1 for r in records if r.get('الحالة') in ('قيد المعالجة', 'جديد'))
        msg = f"📊 *إحصائيات*\nإجمالي المعاملات: {total}\nمكتملة: {completed}\nقيد المعالجة: {pending}"
        await update.message.reply_text(msg, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"خطأ في stats: {e}")
        await update.message.reply_text("حدث خطأ.")

async def qr_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    transaction_id = None
    if sheets_client:
        try:
            ws = sheets_client.get_worksheet(Config.SHEET_USERS)
            if ws:
                records = ws.get_all_records()
                for row in records:
                    if str(row.get('chat_id')) == str(user_id):
                        transaction_id = row.get('transaction_id')
                        break
        except Exception as e:
            logger.error(f"خطأ في جلب معاملة المستخدم: {e}")

    if transaction_id:
        base_url = request.host_url.rstrip('/')
        verify_link = f"{base_url}/verify-email?transaction_id={transaction_id}"
        qr_base64 = QRGenerator.generate_qr(verify_link)
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=base64.b64decode(qr_base64),
            caption=f"📱 *رمز QR للوصول إلى المعاملة*\n\n🆔 {transaction_id}\n\n1️⃣ امسح الرمز أو اضغط الرابط\n2️⃣ أدخل بريدك الجامعي (ينتهي بـ @it.jan.ah)\n3️⃣ سيتم توجيهك إلى صفحة التعديل.\n\n🔗 {verify_link}",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text(
            "📌 *لم يتم ربط حسابك بأي معاملة بعد.*\n\n"
            "لربط حسابك بمعاملة، استخدم الرابط التالي:\n"
            f"`https://t.me/{Config.BOT_USERNAME}?start=رقم_المعاملة`\n\n"
            "(استبدل `رقم_المعاملة` برقم المعاملة الخاص بك)",
            parse_mode='Markdown'
        )

async def support_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name or ""
    if Config.ADMIN_CHAT_ID:
        await context.bot.send_message(
            chat_id=Config.ADMIN_CHAT_ID,
            text=f"📩 *رسالة دعم جديدة*\nمن: {user_name} (ID: {user_id})\n\nلطلب مساعدة، يرجى الرد عليه مباشرة.",
            parse_mode='Markdown'
        )
    await update.message.reply_text(
        "📨 تم إرسال طلبك إلى فريق الدعم. سيتم الرد عليك في أقرب وقت.\n"
        "شكراً لتواصلك معنا."
    )

async def analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not sheets_client:
            await update.message.reply_text("⚠️ النظام غير متصل بقاعدة البيانات.")
            return
        if not context.args:
            await update.message.reply_text("الرجاء إدخال رقم المعاملة: /analyze MUT-123456")
            return
        transaction_id = context.args[0]
        data = sheets_client.get_latest_row_by_id_fast(Config.SHEET_MANAGER, transaction_id)
        if not data:
            await update.message.reply_text(f"❌ لا توجد معاملة بالرقم {transaction_id}")
            return
        transaction_data = data
        ws = sheets_client.get_worksheet(Config.SHEET_HISTORY)
        history = []
        if ws:
            records = ws.get_all_records()
            history = [{'time': r.get('timestamp', ''), 'action': r.get('action', ''), 'user': r.get('user', '')}
                       for r in records if str(r.get('ID')) == transaction_id]
            history.sort(key=lambda x: x['time'])
        await update.message.reply_text("🔍 جاري تحليل المعاملة...")
        if ai_assistant:
            analysis = await ai_assistant.analyze_transaction(transaction_data, history)
        else:
            analysis = "❌ خدمة التحليل غير متاحة حالياً (مفتاح API غير موجود)."
        await update.message.reply_text(analysis, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"خطأ في /analyze: {e}", exc_info=True)
        await update.message.reply_text("عذراً، حدث خطأ أثناء التحليل.")

async def smart_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if 'awaiting' in context.user_data:
        awaiting = context.user_data.pop('awaiting')
        if awaiting == 'id':
            context.args = [text]
            await get_id(update, context)
        elif awaiting == 'history':
            context.args = [text]
            await get_history(update, context)
        elif awaiting == 'search':
            context.args = [text]
            await search(update, context)
        elif awaiting == 'analyze':
            context.args = [text]
            await analyze(update, context)
        elif awaiting == 'adv_search':
            # البحث المتقدم
            criteria = {}
            parts = text.split(',')
            for part in parts:
                if ':' in part:
                    key, val = part.split(':', 1)
                    key = key.strip()
                    val = val.strip()
                    if key == 'القسم':
                        criteria['department'] = val
                    elif key == 'الموظف':
                        criteria['employee'] = val
                    elif key == 'الحالة':
                        criteria['status'] = val
            if not criteria:
                await update.message.reply_text("❌ لم يتم التعرف على المعايير. استخدم الصيغة المذكورة.")
                return
            records = sheets_client.get_latest_transactions_fast(Config.SHEET_MANAGER)
            filtered = []
            for r in records:
                match = True
                if 'department' in criteria and criteria['department'].lower() not in r.get('القسم', '').lower():
                    match = False
                if 'employee' in criteria and criteria['employee'].lower() not in r.get('الموظف المسؤول', '').lower():
                    match = False
                if 'status' in criteria and criteria['status'].lower() != r.get('الحالة', '').lower():
                    match = False
                if match:
                    filtered.append(r)
            if not filtered:
                await update.message.reply_text("❌ لا توجد معاملات تطابق المعايير.")
                return
            msg = f"🔍 *نتائج البحث ({len(filtered)} معاملة)*\n"
            for r in filtered[:20]:
                msg += f"• `{r.get('ID')}` - {r.get('اسم صاحب المعاملة الثلاثي')} - {r.get('الحالة')}\n"
            if len(filtered) > 20:
                msg += f"\nو {len(filtered)-20} معاملات أخرى..."
            await update.message.reply_text(msg, parse_mode='Markdown')
        return

    await ai_chat_handler(update, context)

async def ai_chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    user_id = update.effective_user.id
    is_admin = (user_id == Config.ADMIN_CHAT_ID)
    user_name = update.effective_user.first_name or ""

    if is_admin:
        # ردود سريعة للمدير
        msg_lower = user_message.lower()
        if any(word in msg_lower for word in ['جميع المعاملات', 'قائمة المعاملات', 'كل المعاملات', 'عرض الكل']):
            response = get_all_transactions_list()
            await update.message.reply_text(response, parse_mode='Markdown')
            return
        if any(word in msg_lower for word in ['إحصاء', 'إحصائيات', 'stats', 'احصائيات']):
            await stats(update, context)
            return
        if 'مكتملة' in msg_lower:
            response = get_transactions_by_status('مكتملة')
            await update.message.reply_text(response, parse_mode='Markdown')
            return
        if 'قيد المعالجة' in msg_lower:
            response = get_transactions_by_status('قيد المعالجة')
            await update.message.reply_text(response, parse_mode='Markdown')
            return
        if 'جديد' in msg_lower:
            response = get_transactions_by_status('جديد')
            await update.message.reply_text(response, parse_mode='Markdown')
            return
        if 'متأخرة' in msg_lower:
            response = get_transactions_by_status('متأخرة')
            await update.message.reply_text(response, parse_mode='Markdown')
            return
        if 'خطأ' in msg_lower or 'أخطاء' in msg_lower:
            response = get_transactions_with_errors()
            await update.message.reply_text(response, parse_mode='Markdown')
            return
        if 'تحليل' in msg_lower:
            match = re.search(r'MUT-\d{14}-\d{4}', user_message)
            if match:
                transaction_id = match.group()
                context.args = [transaction_id]
                await analyze(update, context)
                return
            else:
                await update.message.reply_text("الرجاء إدخال رقم المعاملة بشكل صحيح: /analyze MUT-123456...")
                return

        # استعلامات ذكية
        if 'قسم' in user_message:
            dept_name = re.search(r'قسم\s+(.+?)(?:\s|$)', user_message)
            if dept_name:
                dept = dept_name.group(1).strip()
                filtered = sheets_client.get_transactions_by_department(dept)
                if filtered:
                    await update.message.reply_text(f"📊 المعاملات في قسم {dept}: {len(filtered)} معاملة")
                else:
                    await update.message.reply_text(f"لا توجد معاملات في قسم {dept}")
                return
        if 'موظف' in user_message:
            emp_name = re.search(r'موظف\s+(.+?)(?:\s|$)', user_message)
            if emp_name:
                emp = emp_name.group(1).strip()
                filtered = sheets_client.get_transactions_by_employee(emp)
                if filtered:
                    await update.message.reply_text(f"📊 المعاملات للموظف {emp}: {len(filtered)} معاملة")
                else:
                    await update.message.reply_text(f"لا توجد معاملات للموظف {emp}")
                return
        if 'متأخرة' in user_message:
            delayed = sheets_client.filter_transactions('manager', status='متأخرة')
            await update.message.reply_text(f"⚠️ عدد المعاملات المتأخرة: {len(delayed)}")
            return

        # استخدام AI
        logger.info(f"🤖 استعلام ذكي من المدير {user_name}: {user_message[:50]}...")
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
        if ai_assistant:
            response = await ai_assistant.get_response(user_message, user_id, user_name)
        else:
            response = "❌ خدمة الذكاء الاصطناعي غير متاحة حالياً."
        await update.message.reply_text(response)
        return

    if not ai_assistant:
        await update.message.reply_text("عذراً، خدمة الذكاء الاصطناعي غير متاحة حالياً.")
        return
    logger.info(f"🤖 استعلام ذكي من {user_name}: {user_message[:50]}...")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    response = await ai_assistant.get_response(user_message, user_id, user_name)
    await update.message.reply_text(response)

# ------------------ إعداد البوت وحلقة الأحداث ------------------
bot_app = None
background_loop = None
loop_thread = None

if Config.TELEGRAM_BOT_TOKEN:
    try:
        bot_app = Application.builder().token(Config.TELEGRAM_BOT_TOKEN).build()
        bot_app.add_handler(CommandHandler("start", start))
        bot_app.add_handler(CommandHandler("id", get_id))
        bot_app.add_handler(CommandHandler("history", get_history))
        bot_app.add_handler(CommandHandler("search", search))
        bot_app.add_handler(CommandHandler("wake", wake))
        bot_app.add_handler(CommandHandler("stats", stats))
        bot_app.add_handler(CommandHandler("qr", qr_command))
        bot_app.add_handler(CommandHandler("support", support_command))
        bot_app.add_handler(CommandHandler("analyze", analyze))
        bot_app.add_handler(CallbackQueryHandler(button_callback))
        bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, smart_handler))
        logger.info("✅ تم بناء البوت وإضافة المعالجات")

        async def init_bot():
            await bot_app.initialize()
            logger.info("✅ تم تهيئة البوت في الحلقة الخلفية")

        def start_background_loop():
            global background_loop
            background_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(background_loop)
            background_loop.run_until_complete(init_bot())
            background_loop.run_forever()

        loop_thread = threading.Thread(target=start_background_loop, daemon=True)
        loop_thread.start()
        logger.info("⏳ انتظار تهيئة البوت في الخلفية...")
        time.sleep(2)
    except Exception as e:
        logger.error(f"❌ فشل إعداد البوت: {e}")
        bot_app = None

# ------------------ Webhook ------------------
@app.route('/webhook', methods=['POST'])
def webhook():
    if bot_app is None or background_loop is None:
        return "Bot not initialized", 500
    try:
        logger.info("📩 تم استقبال طلب webhook")
        json_str = request.get_data(as_text=True)
        logger.debug(f"📦 محتوى webhook: {json_str[:200]}")
        update = Update.de_json(json.loads(json_str), bot_app.bot)
        future = asyncio.run_coroutine_threadsafe(bot_app.process_update(update), background_loop)
        try:
            future.result(timeout=5)
        except Exception as e:
            logger.error(f"❌ خطأ في معالجة التحديث: {e}", exc_info=True)
        return "OK"
    except Exception as e:
        logger.error(f"❌ خطأ في webhook: {e}", exc_info=True)
        return "Error", 500

def set_webhook_sync():
    if bot_app is None or not Config.WEB_APP_URL:
        return
    webhook_url = f"{Config.WEB_APP_URL.rstrip('/')}/webhook"
    token = Config.TELEGRAM_BOT_TOKEN
    try:
        del_resp = requests.post(f"https://api.telegram.org/bot{token}/deleteWebhook")
        if del_resp.status_code == 200:
            logger.info("✅ تم حذف webhook القديم")
        else:
            logger.warning(f"⚠️ فشل حذف webhook القديم: {del_resp.text}")

        resp = requests.post(
            f"https://api.telegram.org/bot{token}/setWebhook",
            data={"url": webhook_url}
        )
        if resp.status_code == 200 and resp.json().get("ok"):
            logger.info(f"✅ Webhook set to {webhook_url}")
        else:
            logger.error(f"❌ فشل تعيين webhook: {resp.text}")
    except Exception as e:
        logger.error(f"❌ خطأ في تعيين webhook: {e}")

if Config.WEB_APP_URL and bot_app:
    def delayed_webhook():
        time.sleep(5)
        set_webhook_sync()
    threading.Thread(target=delayed_webhook).start()
    logger.info("⏳ سيتم تعيين webhook بعد 5 ثوانٍ...")

# ------------------ نقاط نهاية API ------------------
@app.route('/api/submit', methods=['POST'])
def api_submit():
    global sheets_client
    if sheets_client is None:
        logger.error("sheets_client is None, attempting to reconnect...")
        try:
            sheets_client = GoogleSheetsClient()
            global ai_assistant
            try:
                ai_assistant = AIAssistant(sheets_client=sheets_client)
            except Exception as e:
                logger.error(f"Failed to reinit AI: {e}")
        except Exception as e:
            logger.error(f"Reconnection failed: {e}")
            return jsonify({'success': False, 'error': 'النظام غير متصل بقاعدة البيانات'}), 500

    try:
        name = request.form.get('name', '').strip()
        phone = request.form.get('phone', '').strip()
        function = request.form.get('function', '').strip()
        department = request.form.get('department', '').strip()
        transaction_type = request.form.get('transaction_type', '').strip()
        attachments_text = request.form.get('attachments_text', '').strip()
        uploaded_file = request.files.get('attachment_file')
        attachments = attachments_text
        if uploaded_file and uploaded_file.filename:
            file_data = uploaded_file.read()
            file_link = sheets_client.upload_file_to_drive(file_data, uploaded_file.filename)
            if file_link:
                attachments = attachments_text + "\n" + file_link if attachments_text else file_link

        now = datetime.now()
        timestamp = now.strftime("%Y-%m-%d %H:%M:%S")

        if not name or not phone:
            return jsonify({'success': False, 'error': 'الاسم والهاتف مطلوبان'}), 400

        if not sheets_client:
            return jsonify({'success': False, 'error': 'النظام غير متصل بقاعدة البيانات'}), 500

        ws = sheets_client.get_worksheet(Config.SHEET_MANAGER)
        if not ws:
            logger.error("❌ ورقة manager غير موجودة")
            return jsonify({'success': False, 'error': 'ورقة manager غير موجودة'}), 500

        now_id = datetime.now()
        date_str = now_id.strftime("%Y%m%d%H%M%S")
        random_part = random.randint(1000, 9999)
        transaction_id = f"MUT-{date_str}-{random_part}"

        headers = ws.row_values(1)  # أو استخدام cached headers إذا أردت
        new_row = [''] * len(headers)
        base_url = request.host_url.rstrip('/')
        edit_link = f"{base_url}/transaction/{transaction_id}"
        hyperlink_formula = f'=HYPERLINK("{edit_link}", "تعديل المعاملة")'

        for idx, header in enumerate(headers):
            if header == 'Timestamp':
                new_row[idx] = timestamp
            elif header == 'اسم صاحب المعاملة الثلاثي':
                new_row[idx] = name
            elif header == 'رقم الهاتف':
                new_row[idx] = phone
            elif header == 'الوظيفة':
                new_row[idx] = function
            elif header == 'القسم':
                new_row[idx] = department
            elif header == 'نوع المعاملة':
                new_row[idx] = transaction_type
            elif header == 'المرافقات':
                new_row[idx] = attachments
            elif header == 'ID':
                new_row[idx] = transaction_id
            elif header == 'الرابط':
                new_row[idx] = hyperlink_formula

        # الكتابة إلى manager (متزامنة لأننا بحاجة إلى التأكيد)
        ws.append_row(new_row)
        logger.info(f"✅ تمت كتابة المعاملة {transaction_id} في ورقة manager")

        # إضافة إلى شيت القسم (غير متزامن)
        if department:
            rate_limit_write()
            executor.submit(sheets_client.append_to_department_sheet, department, new_row, headers)
            logger.debug(f"📌 تم إرسال مهمة كتابة شيت القسم {department} إلى الخلفية")

        # إضافة إلى QR (غير متزامن)
        def update_qr():
            qr_ws = sheets_client.get_worksheet(Config.SHEET_QR)
            if qr_ws:
                verify_link = f"{base_url}/verify-email?transaction_id={transaction_id}"
                qr_image_url = f"{base_url}/qr_image/{transaction_id}"
                qr_ws.append_row([transaction_id, f'=IMAGE("{qr_image_url}")', f'=HYPERLINK("{edit_link}", "تعديل المعاملة")'])
                logger.debug(f"✅ تم تحديث QR للمعاملة {transaction_id}")
        rate_limit_write()
        executor.submit(update_qr)

        # إضافة إلى history (غير متزامن)
        rate_limit_write()
        executor.submit(sheets_client.add_history_entry, transaction_id, "تم إنشاء المعاملة", "النظام (API)")

        # إرسال إشعار للمدير (غير متزامن)
        if Config.ADMIN_CHAT_ID and background_loop and bot_app:
            asyncio.run_coroutine_threadsafe(
                bot_app.bot.send_message(
                    chat_id=Config.ADMIN_CHAT_ID,
                    text=f"🆕 *معاملة جديدة*\nالاسم: {name}\nالهاتف: {phone}\nID: {transaction_id}\nالوظيفة: {function}\nالقسم: {department}",
                    parse_mode='Markdown'
                ),
                background_loop
            )

        return jsonify({
            'success': True,
            'id': transaction_id,
            'view_link': f"{base_url}/view/{transaction_id}",
            'deep_link': f"https://t.me/{Config.BOT_USERNAME}?start={transaction_id}"
        })

    except Exception as e:
        logger.error(f"🔥 خطأ في /api/submit: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500

# ------------------ باقي المسارات (مختصرة، ولكنها موجودة في الكود السابق) ------------------
# يجب تضمين جميع المسارات التالية: /api/headers, /api/transactions, /api/transaction/<id>, /api/history/<id>,
# /verify-email, /transaction/<id>, /, /qr/<id>, /qr_image/<id>, /register, /verify, /view/<id>
# مع الحفاظ على الكود كما هو في الإصدارات السابقة (المزودة بواجهات HTML المحسنة).
# نظراً لضيق المساحة، لا أعيد كتابتها هنا، ولكنها متوفرة في الردود السابقة.

# ------------------ معالجة المعاملات الجديدة ------------------
last_row_count = 0

def process_new_transaction(ws, row_number, new_row, transaction_id):
    # ... (نفس الكود السابق)
    pass

def check_new_transactions():
    global last_row_count
    # ... (نفس الكود السابق)

# ------------------ جدولة المهام ------------------
if sheets_client:
    try:
        last_row_count = len(sheets_client.get_latest_transactions_fast(Config.SHEET_MANAGER))
    except Exception as e:
        logger.error(f"❌ فشل قراءة العدد الأولي: {e}")
        last_row_count = 0

    scheduler = BackgroundScheduler()
    scheduler.start()
    scheduler.add_job(
        func=check_new_transactions,
        trigger=IntervalTrigger(seconds=30),
        id='check_transactions',
        replace_existing=True
    )
    logger.info("🔍 بدأت مراقبة المعاملات الجديدة (كل 30 ثانية)")
    atexit.register(lambda: scheduler.shutdown())
    atexit.register(lambda: executor.shutdown(wait=False))

# ------------------ تشغيل التطبيق ------------------
if __name__ == "__main__":
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
