FROM python:3.10-slim

# تثبيت الحزم الأساسية للنظام لتشغيل مكتبة قراءة الـ QR بكفاءة
RUN apt-get update && apt-get install -y \
    libzbar0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# نسخ ملفات المشروع بالكامل إلى السيرفر
COPY . /app

# تثبيت مكتبات بايثون المطلوبة للبوت
RUN pip install --no-cache-dir pyTelegramBotAPI qrcode pyzbar pillow

# أمر تشغيل البوت مباشرة
CMD ["python", "bot.py"]
