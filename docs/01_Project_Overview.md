# 01 — Project Overview

**AI Crypto Signal Pro**
**Document type:** Owner-facing overview (non-technical) / مالک کے لیے تعارفی دستاویز
**Status:** Beta
**Last updated:** 2026-07-28

> 🇬🇧 This document is written for you, the owner of this software — not for a programmer. Every explanation below assumes no coding background. If a word needs technical meaning, it is explained in plain language the first time it appears.
>
> 🇵🇰 یہ دستاویز آپ کے لیے لکھی گئی ہے — سافٹ ویئر کے مالک کے لیے، نہ کہ کسی پروگرامر کے لیے۔ نیچے دی گئی ہر وضاحت اس فرض پر لکھی گئی ہے کہ آپ کو کوڈنگ کا کوئی علم نہیں۔ جہاں کوئی تکنیکی لفظ آئے گا، وہاں پہلی بار آسان زبان میں سمجھایا جائے گا۔

---

## 1. Project Introduction

### 🇬🇧 What is AI Crypto Signal Pro?

AI Crypto Signal Pro is a personal trading intelligence system. It watches financial markets for you around the clock, analyzes what it sees using a mix of proven technical-analysis techniques and an AI scoring system, and presents you with trade ideas, explanations, and decision support — all inside a desktop application built specifically for you.

It covers three kinds of markets:

| Market type | Examples | How it's tracked |
|---|---|---|
| Major cryptocurrencies | Bitcoin, Ethereum, Solana, and roughly 45 other liquid coins | Live Binance futures data |
| Commodities | Gold, Silver, Crude Oil, Brent Oil | Live Binance futures data (same pipeline as crypto) |
| Smaller/newer tokens | Any coin traded on a decentralized exchange, searched by name or contract address | On-chain and DEX data providers |

### 🇵🇰 اے آئی کرپٹو سگنل پرو کیا ہے؟

اے آئی کرپٹو سگنل پرو ایک ذاتی ٹریڈنگ انٹیلی جنس سسٹم ہے۔ یہ آپ کے لیے ہر وقت مارکیٹ پر نظر رکھتا ہے، ثابت شدہ ٹیکنیکل تجزیے کے طریقوں اور ایک AI اسکورنگ سسٹم کی مدد سے جو کچھ دیکھتا ہے اُس کا تجزیہ کرتا ہے، اور آپ کو ٹریڈنگ آئیڈیاز، وجوہات اور فیصلہ کرنے میں مدد فراہم کرتا ہے — یہ سب کچھ ایک ڈیسک ٹاپ ایپلیکیشن کے اندر ہوتا ہے جو خاص طور پر آپ کے لیے بنائی گئی ہے۔

یہ تین طرح کی مارکیٹس کو کور کرتا ہے:

- **بڑی کرپٹو کرنسیز:** بٹ کوائن، ایتھیریم، سولانا، اور تقریباً 45 دیگر مقبول کوائنز — بنانس (Binance) کے لائیو ڈیٹا کے ذریعے۔
- **کموڈٹیز:** سونا، چاندی، کروڈ آئل، برینٹ آئل — یہ بھی وہی بنانس ڈیٹا سسٹم استعمال کرتے ہیں جو کرپٹو استعمال کرتا ہے۔
- **چھوٹے/نئے ٹوکنز:** کوئی بھی کوائن جو ڈی سینٹرلائزڈ ایکسچینج پر ٹریڈ ہو رہا ہو، جسے آپ نام یا کنٹریکٹ ایڈریس سے تلاش کر سکتے ہیں۔

### 🇬🇧 Why was it built?

It was built to solve a personal problem: making trading decisions is hard, time-consuming, and emotionally difficult when done from gut feeling alone. Off-the-shelf "signal" services are usually a black box — you get a "BUY" alert with no explanation, no way to check the reasoning, and no way to know if the signal-provider is trustworthy. This project exists to replace that black box with a system that is transparent, explains itself, and is built entirely around your own trading needs rather than a generic audience.

### 🇵🇰 یہ کیوں بنایا گیا؟

یہ ایک ذاتی مسئلے کو حل کرنے کے لیے بنایا گیا: صرف اندازے یا جذبات کی بنیاد پر ٹریڈنگ کے فیصلے کرنا مشکل، وقت طلب اور ذہنی طور پر تھکا دینے والا کام ہے۔ عام "سگنل" سروسز اکثر ایک بلیک باکس کی طرح ہوتی ہیں — آپ کو صرف "BUY" کا پیغام ملتا ہے، کوئی وجہ نہیں بتائی جاتی، اور آپ کے پاس یہ جانچنے کا کوئی طریقہ نہیں ہوتا کہ سگنل دینے والا قابلِ بھروسہ ہے یا نہیں۔ یہ پروجیکٹ اُس بلیک باکس کی جگہ ایک ایسا نظام لانے کے لیے بنایا گیا جو شفاف ہو، اپنی وجہ خود بتائے، اور خاص طور پر آپ کی اپنی ٹریڈنگ ضروریات کے مطابق بنایا گیا ہو، نہ کہ عام لوگوں کے لیے۔

### 🇬🇧 Who is it for?

It is for **you** — an active, hands-on trader who wants:

- Data-driven trade ideas instead of guesswork.
- Clear reasoning behind every suggestion, not just a signal.
- One place that brings together technical analysis, on-chain safety checks, portfolio risk, and performance tracking.
- A tool that tells you honestly when it doesn't know something, instead of making something up.

### 🇵🇰 یہ کس کے لیے ہے؟

یہ **آپ** کے لیے ہے — ایک ایسے فعال ٹریڈر کے لیے جو یہ چاہتا ہے:

- اندازوں کی بجائے ڈیٹا پر مبنی ٹریڈنگ آئیڈیاز۔
- صرف سگنل نہیں بلکہ ہر تجویز کے پیچھے واضح وجہ۔
- ایک ہی جگہ پر ٹیکنیکل تجزیہ، آن چین سیفٹی چیکس، پورٹ فولیو رسک، اور کارکردگی کی نگرانی۔
- ایسا ٹول جو ایمانداری سے بتائے کہ کب اُسے کچھ معلوم نہیں، بجائے اس کے کہ کوئی جھوٹا جواب بنا دے۔

### 🇬🇧 Who should NOT use it?

- Anyone looking for a "guaranteed profit" system — this does not exist, and this software makes no such claim.
- Anyone who wants to trade fully hands-off with no oversight. Every trade execution is a deliberate action you take; the software never places a real trade on its own.
- Anyone unwilling to also manage their own risk (how much money to risk per trade, when to stop trading for the day, etc.). The software supports these decisions — it does not make them for you.

### 🇵🇰 یہ کن لوگوں کو استعمال نہیں کرنا چاہیے؟

- ایسے لوگ جو "یقینی منافع" والا نظام تلاش کر رہے ہیں — ایسی کوئی چیز موجود نہیں، اور یہ سافٹ ویئر ایسا کوئی دعویٰ نہیں کرتا۔
- ایسے لوگ جو مکمل طور پر خودکار (automatic) ٹریڈنگ چاہتے ہیں، بغیر خود نگرانی کیے۔ ہر ٹریڈ کا اصل عمل (execution) آپ کا اپنا جان بوجھ کر کیا گیا فیصلہ ہوتا ہے؛ سافٹ ویئر خود کبھی کوئی حقیقی ٹریڈ نہیں کرتا۔
- ایسے لوگ جو اپنا رسک خود منظم (manage) نہیں کرنا چاہتے (جیسے ہر ٹریڈ میں کتنا پیسہ لگانا ہے، کب ٹریڈنگ روک دینی ہے)۔ سافٹ ویئر ان فیصلوں میں مدد کرتا ہے — یہ آپ کی جگہ فیصلہ نہیں کرتا۔

---

## 2. Main Purpose

### 🇬🇧 English

The goal of this software is simple to state and hard to build: **turn scattered market information into a clear, honest, explainable trading decision — every time, for every asset it watches.**

What makes it different from an ordinary signal app:

- **It shows its work.** Every recommendation comes with the evidence behind it — which factors were positive, which were negative, and exactly how the confidence score was calculated.
- **It never mixes its jobs.** One part of the system decides ("what should this trade look like?"). A separate part only explains ("why does the system think that?"). A separate part only researches broader market context. A separate part only coaches you on practical questions. They never overlap or duplicate each other's work — see Section 4 for how they connect.
- **It learns from your own results, not guesses.** The AI scoring weights are calibrated using your platform's own real, historical trade outcomes — separately for crypto, gold, silver, and oil, because each behaves differently.
- **It is honest about gaps.** If real data isn't available for something, the software says "Not Available" instead of inventing a plausible-looking number.

### 🇵🇰 اردو

اس سافٹ ویئر کا مقصد کہنے میں آسان مگر بنانا مشکل ہے: **بکھری ہوئی مارکیٹ کی معلومات کو ایک واضح، ایماندار، اور قابلِ وضاحت ٹریڈنگ فیصلے میں بدلنا — ہر بار، ہر اُس چیز کے لیے جس پر یہ نظر رکھتا ہے۔**

یہ عام سگنل ایپس سے کیسے مختلف ہے:

- **یہ اپنا پورا کام دکھاتا ہے۔** ہر تجویز کے ساتھ اُس کی وجوہات بھی دی جاتی ہیں — کون سے عوامل مثبت تھے، کون سے منفی، اور کانفیڈنس اسکور بالکل کیسے نکالا گیا۔
- **یہ اپنے کاموں کو کبھی نہیں ملاتا۔** سسٹم کا ایک حصہ صرف فیصلہ کرتا ہے ("یہ ٹریڈ کیسا ہونا چاہیے؟")۔ ایک الگ حصہ صرف وجہ بتاتا ہے ("سسٹم ایسا کیوں سوچتا ہے؟")۔ ایک الگ حصہ صرف مارکیٹ کے وسیع تناظر پر تحقیق کرتا ہے۔ ایک الگ حصہ صرف عملی سوالات پر رہنمائی کرتا ہے۔ یہ سب ایک دوسرے کا کام دوبارہ نہیں کرتے — سیکشن 4 میں دیکھیں کہ یہ آپس میں کیسے جُڑے ہیں۔
- **یہ اندازوں سے نہیں بلکہ آپ کے اپنے نتائج سے سیکھتا ہے۔** AI کے اسکورنگ وزن (weights) آپ کے پلیٹ فارم کے اپنے حقیقی، پرانے ٹریڈ نتائج سے کیلیبریٹ (calibrate) کیے جاتے ہیں — کرپٹو، سونا، چاندی، اور تیل کے لیے الگ الگ، کیونکہ ہر ایک کا رویہ مختلف ہوتا ہے۔
- **یہ کمیوں کے بارے میں ایماندار ہے۔** اگر کسی چیز کا حقیقی ڈیٹا موجود نہ ہو، تو سافٹ ویئر "Not Available" (دستیاب نہیں) کہتا ہے، بجائے اس کے کہ کوئی بناوٹی مگر معقول لگنے والا نمبر دکھا دے۔

---

## 3. Major Features

### 🇬🇧 English

**Market Scanner**
Continuously scans roughly 50 liquid crypto pairs plus Gold, Silver, and Oil, looking for high-quality trade setups using institutional-style price-action analysis (explained under "AI Decision Engine" below). It automatically filters out thinly-traded coins so you're never shown a setup you couldn't safely trade at size.

**Token Scanner**
A separate scanner for newer or smaller tokens that aren't listed on Binance — the kind of coin you'd find by pasting a contract address or searching a name. Because these tokens carry very different risks (rug pulls, honeypots, fake liquidity), this scanner adds dedicated safety checks that the main Market Scanner doesn't need.

**Technical Dashboard**
The shared "chart-reading" engine behind both scanners. It looks for the same patterns a professional technical trader would look for: market structure breaks, liquidity sweeps, fair-value gaps, supply/demand zones, trend direction across multiple timeframes, and classic indicators (moving averages, RSI, MACD, Bollinger Bands, and others). This dashboard is the foundation every other module builds on.

**Smart Money Dashboard**
Looks at real trade-by-trade order flow to estimate whether buyers or sellers are currently in control — sometimes called reading "smart money" positioning. This is a real measurement of executed trades, not a guess.

**Contract Security Dashboard**
For Token Scanner results only. Checks a token's contract for common scam patterns: can the owner drain the pool, is there a hidden sell tax, is the liquidity locked, how concentrated is ownership among a few wallets. This exists because small tokens carry risks that established coins like Bitcoin simply don't have.

**AI Decision Engine**
The core of the platform. It combines everything the Technical, Smart Money, and (where relevant) Security dashboards found, applies a set of calibrated weights, and produces one clear output: a confidence score, a recommended direction, and suggested entry/stop-loss/take-profit levels. The weighting is calibrated separately for each asset type (crypto vs. gold vs. silver vs. oil) using your own historical trade results — not a one-size-fits-all formula.

**Evidence Engine**
Answers the question "why?" for any decision the AI Decision Engine made. It lists the specific positive and negative factors that went into the score, breaks the confidence percentage down category by category (so you can see exactly what helped and what held it back), and shows the historical win rate for that same asset if enough past trades exist. It never performs new analysis — it only explains analysis that already happened elsewhere.

**Research Engine**
Adds supporting market context — open interest trends, how one-sided the crowd's positioning is, and broad sentiment readings (like the Fear & Greed Index). This is explicitly **not** a trading signal. It exists to give you a fuller picture, and it is always labeled as supporting context rather than a recommendation.

**Trading Coach**
Answers the practical, in-the-moment questions a trader actually asks: *Should I enter now? Should I wait? Should I move my stop loss? Should I take partial profit? Is it too late to enter? Should I scale in? Should I exit?* The Coach never invents its own market view — every answer is reasoned strictly from what the Decision, Evidence, and Research modules already produced, and it never overrides the Decision Engine's own verdict.

**Portfolio Intelligence**
Looks across everything you're currently holding (or every currently active signal, if no account is linked) and reports your real exposure, risk concentration, correlation between your positions (are you accidentally holding five things that all move together?), and how diversified you really are.

**Performance Monitor**
Tracks how the AI's own predictions have actually performed over time — win rate broken down by confidence level, by coin, and by asset class, plus a full trade journal recording the real reasoning behind every closed trade. This is how you (and the system) stay honest about results.

### 🇵🇰 اردو

**مارکیٹ اسکینر (Market Scanner)**
یہ مسلسل تقریباً 50 مقبول کرپٹو جوڑوں کے علاوہ سونا، چاندی اور تیل کو اسکین کرتا ہے، اور ادارہ جاتی سطح کے پرائس ایکشن تجزیے (نیچے "AI Decision Engine" میں وضاحت) کی مدد سے اچھے ٹریڈ سیٹ اپس تلاش کرتا ہے۔ یہ خودکار طور پر کم ٹریڈ ہونے والے کوائنز کو نکال دیتا ہے تاکہ آپ کو کبھی ایسا سیٹ اپ نہ دکھایا جائے جسے آپ محفوظ طریقے سے بڑی مقدار میں ٹریڈ نہ کر سکیں۔

**ٹوکن اسکینر (Token Scanner)**
یہ نئے یا چھوٹے ٹوکنز کے لیے ایک الگ اسکینر ہے جو بنانس پر لسٹ نہیں ہوتے — وہ کوائنز جو آپ کنٹریکٹ ایڈریس یا نام سے تلاش کرتے ہیں۔ چونکہ ایسے ٹوکنز میں مختلف خطرات ہوتے ہیں (رگ پُل، ہنی پاٹ، جعلی لیکویڈیٹی)، یہ اسکینر خصوصی سیفٹی چیکس شامل کرتا ہے جن کی مین مارکیٹ اسکینر کو ضرورت نہیں ہوتی۔

**ٹیکنیکل ڈیش بورڈ (Technical Dashboard)**
یہ دونوں اسکینرز کا مشترکہ "چارٹ پڑھنے" والا انجن ہے۔ یہ وہی پیٹرنز تلاش کرتا ہے جو ایک پیشہ ور ٹیکنیکل ٹریڈر تلاش کرے گا: مارکیٹ اسٹرکچر بریکس، لیکویڈیٹی سویپس، فیئر ویلیو گیپس، سپلائی/ڈیمانڈ زونز، متعدد ٹائم فریمز پر رجحان کی سمت، اور روایتی انڈیکیٹرز (موونگ ایوریج، RSI، MACD، بولنگر بینڈز وغیرہ)۔ یہ ڈیش بورڈ ہر دوسرے ماڈیول کی بنیاد ہے۔

**سمارٹ منی ڈیش بورڈ (Smart Money Dashboard)**
یہ اصل ٹریڈ بہ ٹریڈ آرڈر فلو دیکھ کر اندازہ لگاتا ہے کہ فی الحال خریدار زیادہ طاقتور ہیں یا بیچنے والے — اسے بعض اوقات "سمارٹ منی" کی پوزیشن پڑھنا کہا جاتا ہے۔ یہ حقیقی ہوئے ہوئے ٹریڈز کی پیمائش ہے، کوئی اندازہ نہیں۔

**کنٹریکٹ سیکیورٹی ڈیش بورڈ (Contract Security Dashboard)**
یہ صرف ٹوکن اسکینر کے نتائج کے لیے ہے۔ یہ ٹوکن کے کنٹریکٹ میں عام اسکیم پیٹرنز چیک کرتا ہے: کیا مالک پول خالی کر سکتا ہے، کیا کوئی چھپا ہوا سیل ٹیکس ہے، کیا لیکویڈیٹی لاک ہے، اور ملکیت کتنے کم والٹس میں مرکوز ہے۔ یہ اس لیے ضروری ہے کیونکہ چھوٹے ٹوکنز میں ایسے خطرات ہوتے ہیں جو بٹ کوائن جیسے مضبوط کوائنز میں نہیں ہوتے۔

**اے آئی ڈیسیژن انجن (AI Decision Engine)**
یہ پلیٹ فارم کا مرکزی حصہ ہے۔ یہ ٹیکنیکل، سمارٹ منی، اور (جہاں ضروری ہو) سیکیورٹی ڈیش بورڈز کی تمام معلومات کو ملا کر، کیلیبریٹڈ وزن (weights) لگا کر ایک واضح نتیجہ نکالتا ہے: ایک کانفیڈنس اسکور، تجویز کردہ سمت، اور تجویز کردہ اینٹری/اسٹاپ لاس/ٹیک پرافٹ لیول۔ یہ وزن ہر ایسٹ ٹائپ (کرپٹو، سونا، چاندی، تیل) کے لیے آپ کے اپنے پرانے ٹریڈ نتائج سے الگ الگ کیلیبریٹ کیے جاتے ہیں — کوئی ایک ہی فارمولا سب پر لاگو نہیں ہوتا۔

**ایویڈنس انجن (Evidence Engine)**
یہ AI Decision Engine کے ہر فیصلے کے لیے "کیوں؟" کا جواب دیتا ہے۔ یہ اسکور میں شامل مخصوص مثبت اور منفی عوامل کی فہرست دیتا ہے، کانفیڈنس فیصد کو زمرہ وار (category by category) تقسیم کر کے دکھاتا ہے (تاکہ آپ دیکھ سکیں کہ کس چیز نے مدد کی اور کس چیز نے روکا)، اور اگر کافی پرانے ٹریڈز موجود ہوں تو اُسی کوائن کی تاریخی کامیابی کی شرح بھی دکھاتا ہے۔ یہ کبھی نیا تجزیہ نہیں کرتا — یہ صرف پہلے سے ہو چکے تجزیے کی وضاحت کرتا ہے۔

**ریسرچ انجن (Research Engine)**
یہ اضافی مارکیٹ تناظر فراہم کرتا ہے — اوپن انٹرسٹ کے رجحانات، کریمی طبقے کی پوزیشننگ کتنی یکطرفہ ہے، اور مجموعی جذباتی کیفیت (جیسے Fear & Greed Index)۔ یہ واضح طور پر ٹریڈنگ سگنل **نہیں** ہے۔ یہ آپ کو مکمل تصویر دینے کے لیے موجود ہے، اور ہمیشہ ایک معاون معلومات کے طور پر لیبل کیا جاتا ہے، نہ کہ تجویز کے طور پر۔

**ٹریڈنگ کوچ (Trading Coach)**
یہ وہ عملی، فوری سوالات کا جواب دیتا ہے جو ایک ٹریڈر واقعی پوچھتا ہے: *کیا مجھے ابھی اینٹری لینی چاہیے؟ کیا مجھے انتظار کرنا چاہیے؟ کیا مجھے اسٹاپ لاس منتقل کرنا چاہیے؟ کیا مجھے جزوی منافع لینا چاہیے؟ کیا اینٹری میں دیر ہو چکی ہے؟ کیا مجھے مزید شامل کرنا چاہیے؟ کیا مجھے نکل جانا چاہیے؟* کوچ کبھی اپنی طرف سے نئی مارکیٹ رائے نہیں بناتا — ہر جواب سختی سے Decision، Evidence، اور Research ماڈیولز کی پہلے سے موجود معلومات پر مبنی ہوتا ہے، اور یہ کبھی بھی Decision Engine کے فیصلے کو نظرانداز نہیں کرتا۔

**پورٹ فولیو انٹیلی جنس (Portfolio Intelligence)**
یہ آپ کی موجودہ تمام ہولڈنگز (یا اگر اکاؤنٹ منسلک نہیں تو تمام فعال سگنلز) کا جائزہ لے کر آپ کا حقیقی ایکسپوژر (exposure)، رسک کا ارتکاز، آپ کی پوزیشنز کے درمیان تعلق (correlation) — کہیں آپ نے بلا ارادہ پانچ ایسی چیزیں تو نہیں رکھی ہوئیں جو ایک ساتھ حرکت کرتی ہیں — اور آپ واقعی کتنے متنوع (diversified) ہیں، یہ سب رپورٹ کرتا ہے۔

**پرفارمنس مانیٹر (Performance Monitor)**
یہ وقت کے ساتھ AI کی پیشگوئیوں کی حقیقی کارکردگی کو ٹریک کرتا ہے — کانفیڈنس لیول، کوائن، اور ایسٹ کلاس کے لحاظ سے جیت کی شرح، اور ایک مکمل ٹریڈ جرنل جس میں ہر بند شدہ ٹریڈ کی اصل وجہ درج ہوتی ہے۔ اسی طرح آپ (اور خود سسٹم) نتائج کے بارے میں ایماندار رہتے ہیں۔

---

## 4. High-Level Workflow

### Diagram

```
                     Live Market Data
                  (Binance price/volume feed,
                   on-chain data for tokens)
                            │
                            ▼
                     ┌─────────────┐
                     │   Scanner   │   (Market Scanner or Token Scanner)
                     └─────────────┘
                            │
                            ▼
              ┌──────────────────────────┐
              │   Technical Dashboard    │   price-action + indicators
              │  (+ Smart Money, + Security
              │     for Token Scanner)   │
              └──────────────────────────┘
                            │
                            ▼
                  ┌───────────────────┐
                  │ AI Decision Engine │   scores + final decision
                  └───────────────────┘
                            │
              ┌─────────────┼─────────────┐
              ▼             ▼             ▼
       ┌────────────┐ ┌───────────┐ ┌──────────────┐
       │  Evidence  │ │ Research  │ │Trading Coach │
       │   Engine   │ │  Engine   │ │ (on request) │
       └────────────┘ └───────────┘ └──────────────┘
              │             │             │
              └─────────────┼─────────────┘
                            ▼
                  ┌───────────────────┐
                  │  Desktop Application │
                  │  (what you actually see)
                  └───────────────────┘
                            │
                            ▼
                           You
                  (the trading decision
                   is always yours)
```

Running in parallel, two more modules watch your account and history rather than the live market:

```
Your Positions / Signal History
            │
            ├──► Portfolio Intelligence  (exposure, risk, correlation)
            │
            └──► Performance Monitor     (win rate, calibration, journal)
```

### 🇬🇧 English — Step by step

1. **Market data arrives.** Real prices and trade volume, continuously.
2. **The Scanner picks it up** for every asset it tracks.
3. **The Technical Dashboard reads the chart** the way an experienced trader would.
4. **(For Token Scanner) Smart Money and Security checks run alongside it**, since small tokens need extra scrutiny.
5. **The AI Decision Engine scores everything** and produces one final answer: how confident is this setup, and what would the trade look like?
6. **The Evidence Engine explains the score.** The Research Engine adds context. The Trading Coach is available whenever you ask it a practical question.
7. **Everything is shown to you in the desktop app** — nothing happens without you seeing it.
8. **You decide.** The software never trades on its own; execution is always a deliberate action you take.

### 🇵🇰 اردو — قدم بہ قدم

1. **مارکیٹ ڈیٹا آتا ہے۔** حقیقی قیمتیں اور ٹریڈنگ حجم، مسلسل۔
2. **اسکینر اسے پکڑتا ہے** ہر اُس اثاثے (asset) کے لیے جسے یہ ٹریک کرتا ہے۔
3. **ٹیکنیکل ڈیش بورڈ چارٹ پڑھتا ہے**، بالکل ویسے جیسے ایک تجربہ کار ٹریڈر پڑھے گا۔
4. **(ٹوکن اسکینر کے لیے) سمارٹ منی اور سیکیورٹی چیکس ساتھ ساتھ چلتے ہیں**، کیونکہ چھوٹے ٹوکنز کو اضافی جانچ کی ضرورت ہوتی ہے۔
5. **AI Decision Engine ہر چیز کا اسکور نکالتا ہے** اور ایک حتمی جواب دیتا ہے: یہ سیٹ اپ کتنا قابلِ اعتماد ہے، اور ٹریڈ کیسا ہونا چاہیے؟
6. **Evidence Engine اسکور کی وجہ بتاتا ہے۔** Research Engine اضافی تناظر شامل کرتا ہے۔ Trading Coach جب بھی آپ کوئی عملی سوال پوچھیں، دستیاب ہوتا ہے۔
7. **یہ سب کچھ آپ کو ڈیسک ٹاپ ایپ میں دکھایا جاتا ہے** — کچھ بھی آپ کی نظر کے بغیر نہیں ہوتا۔
8. **فیصلہ آپ کرتے ہیں۔** سافٹ ویئر خود کبھی ٹریڈ نہیں کرتا؛ عمل درآمد (execution) ہمیشہ آپ کا جان بوجھ کر کیا گیا فیصلہ ہوتا ہے۔

**🇬🇧 Portfolio Intelligence and Performance Monitor** run alongside this main flow, watching your account and trade history rather than the live market, so you always know your real risk and real track record.

**🇵🇰 پورٹ فولیو انٹیلی جنس اور پرفارمنس مانیٹر** اس مرکزی عمل کے ساتھ ساتھ چلتے ہیں، یہ آپ کے اکاؤنٹ اور ٹریڈ ہسٹری پر نظر رکھتے ہیں نہ کہ لائیو مارکیٹ پر، تاکہ آپ کو ہمیشہ اپنے حقیقی رسک اور حقیقی کارکردگی کا علم رہے۔

---

## 5. Software Philosophy

### 🇬🇧 English

These are the principles this software was built on, from day one. They are not slogans — every one of them has shaped real design decisions in the project.

- **No fake data.** Every number you see traces back to a real source. If the software cannot get real data for something, it says so.
- **No fake AI.** The Trading Coach and AI Assistant never generate their own market opinion out of thin air. They only reason over analysis the platform already performed.
- **Evidence before recommendations.** A decision without a reason attached is not considered complete.
- **Explain every decision.** Confidence scores are always broken down into what contributed and what held them back — never a single unexplained number.
- **Honest "Not Available."** Guessing is never acceptable. If data is missing, the software tells you plainly rather than filling the gap with something that merely looks reasonable.
- **Modular architecture.** Each engine has exactly one job (scoring, explaining, researching, coaching, and so on) and none of them duplicate each other's work.
- **Reuse, don't rebuild.** New features are built on top of the existing, already-tested Decision Engine rather than creating a second, competing scoring system. This keeps the platform's core logic consistent everywhere it's used.
- **Nothing is removed without your approval.** Even dead or unused code is left in place and flagged for your review rather than deleted unilaterally.

### 🇵🇰 اردو

یہ وہ اصول ہیں جن پر یہ سافٹ ویئر پہلے دن سے بنایا گیا ہے۔ یہ صرف نعرے نہیں — ان میں سے ہر ایک نے پروجیکٹ کے حقیقی فیصلوں کو شکل دی ہے۔

- **کوئی جعلی ڈیٹا نہیں۔** آپ کو نظر آنے والا ہر نمبر ایک حقیقی ذریعے سے آتا ہے۔ اگر سافٹ ویئر کسی چیز کا حقیقی ڈیٹا حاصل نہیں کر سکتا، تو یہ صاف بتا دیتا ہے۔
- **کوئی جعلی AI نہیں۔** Trading Coach اور AI Assistant کبھی بھی اپنی طرف سے کوئی مارکیٹ رائے نہیں بناتے۔ یہ صرف اُس تجزیے پر سوچتے ہیں جو پلیٹ فارم پہلے ہی کر چکا ہے۔
- **سفارش سے پہلے وجہ۔** بغیر وجہ کے کوئی فیصلہ مکمل نہیں سمجھا جاتا۔
- **ہر فیصلے کی وضاحت۔** کانفیڈنس اسکور ہمیشہ اس بات میں تقسیم کیا جاتا ہے کہ کس چیز نے مدد کی اور کس چیز نے روکا — کبھی بھی صرف ایک بغیر وضاحت کے نمبر نہیں دیا جاتا۔
- **ایماندار "دستیاب نہیں"۔** اندازہ لگانا کبھی قابلِ قبول نہیں۔ اگر ڈیٹا موجود نہیں تو سافٹ ویئر صاف صاف بتاتا ہے، بجائے اس کے کہ خلا کو کسی معقول نظر آنے والی چیز سے بھر دے۔
- **ماڈیولر (الگ الگ حصوں پر مبنی) ڈھانچہ۔** ہر انجن کا صرف ایک کام ہے (اسکورنگ، وضاحت، تحقیق، رہنمائی وغیرہ) اور کوئی بھی ایک دوسرے کا کام دوبارہ نہیں کرتا۔
- **دوبارہ استعمال کریں، دوبارہ نہ بنائیں۔** نئی خصوصیات ہمیشہ موجودہ، پہلے سے آزمائے ہوئے Decision Engine پر بنائی جاتی ہیں، نہ کہ ایک نیا مقابل اسکورنگ سسٹم بنا کر۔ اس سے پلیٹ فارم کی بنیادی منطق ہر جگہ ایک جیسی رہتی ہے۔
- **آپ کی اجازت کے بغیر کچھ نہیں ہٹایا جاتا۔** یہاں تک کہ غیر استعمال شدہ کوڈ بھی موجود رکھا جاتا ہے اور آپ کے جائزے کے لیے نشان زد کیا جاتا ہے، اکیلے فیصلہ کر کے نہیں ہٹایا جاتا۔

---

## 6. What This Software Does NOT Do

### 🇬🇧 English

Said plainly, so there is no ambiguity:

- It does **not** predict the future with certainty. It estimates probability based on patterns, nothing more.
- It does **not** guarantee profit. No software can.
- It does **not** replace your own risk management. It gives you the tools (position sizing, portfolio risk, correlation warnings) — you still decide how much to risk.
- It does **not** trade on your behalf automatically. Every real trade execution is a specific, deliberate action you take.
- It is **not** licensed financial advice, and nothing it produces should be treated as such.
- It does **not** currently have a login screen on the desktop app — this is a known, planned piece of future work, not an oversight (see Section 7).

### 🇵🇰 اردو

صاف الفاظ میں، تاکہ کوئی ابہام نہ رہے:

- یہ مستقبل کی یقینی پیشگوئی **نہیں** کرتا۔ یہ صرف پیٹرنز کی بنیاد پر امکان کا اندازہ لگاتا ہے، اس سے زیادہ کچھ نہیں۔
- یہ منافع کی ضمانت **نہیں** دیتا۔ کوئی بھی سافٹ ویئر یہ نہیں دے سکتا۔
- یہ آپ کے اپنے رسک مینجمنٹ کی جگہ **نہیں** لیتا۔ یہ آپ کو اوزار دیتا ہے (پوزیشن سائزنگ، پورٹ فولیو رسک، کوریلیشن وارننگز) — کتنا رسک لینا ہے، یہ فیصلہ اب بھی آپ کا ہے۔
- یہ آپ کی طرف سے خودکار طریقے سے ٹریڈ **نہیں** کرتا۔ ہر حقیقی ٹریڈ کا عمل درآمد آپ کا اپنا، جان بوجھ کر کیا گیا فیصلہ ہوتا ہے۔
- یہ کوئی لائسنس یافتہ مالی مشورہ **نہیں** ہے، اور اس کی کسی بھی چیز کو ایسا نہیں سمجھنا چاہیے۔
- فی الحال ڈیسک ٹاپ ایپ میں لاگ اِن اسکرین **موجود نہیں** — یہ ایک معلوم، منصوبہ بند مستقبل کا کام ہے، کوئی بھول چوک نہیں (سیکشن 7 دیکھیں)۔

---

## 7. Current Project Status

### 🇬🇧 English

**Overall status: Beta.**

| Area | Status |
|---|---|
| Market Scanner (crypto + commodities) | Complete |
| Token Scanner (on-chain tokens) | Complete |
| Technical / Smart Money / Contract Security Dashboards | Complete |
| AI Decision Engine + per-asset calibration | Complete |
| Evidence Engine | Complete |
| Research Engine | Complete |
| Trading Coach | Complete |
| Portfolio Intelligence | Complete |
| Performance Monitor | Complete |
| Desktop app screens for all of the above | Complete, awaiting your own hands-on testing |
| Full project audit (bugs, performance, security, consistency) | Complete — zero blocking issues found |
| Production-deployment safety checks | Built and documented, not yet applied (only needed if this leaves your own machine) |
| Desktop login/authentication | Not yet built (backend supports it; desktop app does not use it yet) |

**What remains before Version 1.0:**

1. You confirming, on your own machine, that the newest desktop screens build and run correctly.
2. Deciding whether to remove a couple of small, already-identified unused screens left over from earlier development.
3. If you ever plan to run this somewhere other than your own computer: generating a proper security key and turning on login, following the checklist already prepared for that.

None of the above are urgent bugs — they are simply the last checklist items before calling this "1.0."

### 🇵🇰 اردو

**مجموعی حیثیت: بیٹا (Beta)**

| شعبہ | حیثیت |
|---|---|
| مارکیٹ اسکینر (کرپٹو + کموڈٹیز) | مکمل |
| ٹوکن اسکینر (آن چین ٹوکنز) | مکمل |
| ٹیکنیکل / سمارٹ منی / کنٹریکٹ سیکیورٹی ڈیش بورڈز | مکمل |
| AI Decision Engine + ہر ایسٹ کے لیے کیلیبریشن | مکمل |
| Evidence Engine | مکمل |
| Research Engine | مکمل |
| Trading Coach | مکمل |
| Portfolio Intelligence | مکمل |
| Performance Monitor | مکمل |
| مذکورہ بالا سب کی ڈیسک ٹاپ اسکرینیں | مکمل، آپ کے اپنے ٹیسٹ کا انتظار |
| مکمل پروجیکٹ آڈٹ (بگز، کارکردگی، سیکیورٹی، مطابقت) | مکمل — کوئی رکاوٹ ڈالنے والا مسئلہ نہیں ملا |
| پروڈکشن ڈیپلائمنٹ سیفٹی چیکس | بن چکے اور دستاویز میں موجود، ابھی لاگو نہیں کیے گئے (صرف اُس وقت ضروری جب یہ آپ کے اپنے کمپیوٹر سے باہر چلایا جائے) |
| ڈیسک ٹاپ لاگ اِن/تصدیق (authentication) | ابھی نہیں بنایا گیا (بیک اینڈ اس کی حمایت کرتا ہے؛ ڈیسک ٹاپ ایپ ابھی اسے استعمال نہیں کرتی) |

**ورژن 1.0 سے پہلے کیا باقی ہے:**

1. آپ اپنے کمپیوٹر پر تصدیق کریں کہ نئی ڈیسک ٹاپ اسکرینیں درست طریقے سے بلڈ اور رن ہوتی ہیں۔
2. یہ فیصلہ کرنا کہ کیا پرانے ڈیویلپمنٹ سے بچی ہوئی چند چھوٹی، پہلے سے شناخت شدہ غیر استعمال شدہ اسکرینز کو ہٹانا ہے۔
3. اگر آپ کبھی اسے اپنے کمپیوٹر کے علاوہ کہیں اور چلانے کا ارادہ رکھتے ہیں: ایک مناسب سیکیورٹی کی (key) بنانا اور لاگ اِن آن کرنا، پہلے سے تیار کردہ چیک لسٹ کے مطابق۔

مندرجہ بالا میں سے کوئی بھی فوری بگ نہیں ہے — یہ صرف "1.0" کہلانے سے پہلے آخری چیک لسٹ کی چیزیں ہیں۔

---

## 8. Future Vision

### 🇬🇧 English

The long-term direction for this project, based on what has actually been planned so far (not speculation):

- **Version 1.0** follows once the Beta checklist above is confirmed complete.
- **The AI keeps improving from your own results.** As you close more real trades, the calibration system automatically recalibrates its weighting per asset class — the platform gets better tuned to real outcomes over time, without needing new code.
- **A proper login for the desktop app** is the next concrete piece of planned work, so the application can be used safely outside of your own machine if you ever choose to.
- **Production deployment support already exists** (secure configuration templates, containerized setup) for whenever you're ready to run this somewhere other than your own computer.

This document will be updated if the roadmap changes. It intentionally does not list features that haven't actually been planned.

### 🇵🇰 اردو

اس پروجیکٹ کی طویل مدتی سمت، جو اب تک واقعی منصوبہ بند چیزوں پر مبنی ہے (قیاس آرائی نہیں):

- **ورژن 1.0** اُس وقت آئے گا جب اوپر دی گئی بیٹا چیک لسٹ مکمل ہونے کی تصدیق ہو جائے۔
- **AI آپ کے اپنے نتائج سے بہتر ہوتا رہتا ہے۔** جیسے جیسے آپ زیادہ حقیقی ٹریڈز بند کرتے ہیں، کیلیبریشن سسٹم خودکار طور پر ہر ایسٹ کلاس کے لیے اپنے وزن (weights) دوبارہ ترتیب دیتا ہے — پلیٹ فارم وقت کے ساتھ حقیقی نتائج کے مطابق بہتر ہوتا جاتا ہے، بغیر کسی نئے کوڈ کی ضرورت کے۔
- **ڈیسک ٹاپ ایپ کے لیے مناسب لاگ اِن** اگلا واضح منصوبہ بند کام ہے، تاکہ ایپلیکیشن کو ضرورت پڑنے پر آپ کے کمپیوٹر سے باہر بھی محفوظ طریقے سے استعمال کیا جا سکے۔
- **پروڈکشن ڈیپلائمنٹ کی سہولت پہلے سے موجود ہے** (محفوظ کنفیگریشن ٹیمپلیٹس، کنٹینرائزڈ سیٹ اپ) جب بھی آپ اسے اپنے کمپیوٹر کے علاوہ کہیں اور چلانے کے لیے تیار ہوں۔

اگر روڈ میپ تبدیل ہوا تو یہ دستاویز اپ ڈیٹ کی جائے گی۔ اس میں جان بوجھ کر ایسی خصوصیات شامل نہیں کی گئیں جو حقیقت میں منصوبہ بند نہیں ہیں۔

---

## 9. Owner Notes

### 🇬🇧 English — *This section is for you.*

**What to remember:**

- This is a decision-support tool, not an autopilot. Every screen is built to inform your judgment, not replace it.
- When you see "Not Available" anywhere in the app, that is the software being honest with you — treat it as useful information, not a bug.
- The confidence percentage on a signal is a probability, not a promise. A well-reasoned 85% confidence trade can still lose; that is normal and expected over a large enough sample.
- The Trading Coach is only as good as the data underneath it. If the Evidence or Research sections show gaps for a particular asset, treat the Coach's answer with that same caution.
- Use Portfolio Intelligence regularly, especially the correlation and exposure sections — it's easy to feel diversified while actually holding several positions that move together.

**How to think about the software:**

Think of it as a very well-organized, very honest research assistant — one that never gets tired, never gets emotional, and always shows its reasoning. It does the information-gathering and analysis. You still make the final call.

**Mistakes to avoid:**

- Don't treat a high confidence score as certainty — it is still an estimate.
- Don't disable or ignore the risk/correlation warnings; they exist because a past version of this project's own audits found real gaps that needed exactly these checks.
- Don't assume "AI" means "infallible." The whole design of this platform — evidence, explanations, honest gaps — exists specifically because AI outputs need to be checked, not blindly trusted.
- Before ever running this platform anywhere other than your own machine, follow the security checklist first (a real secret key, login turned on). Skipping this step is the one item on the entire checklist with real consequences if missed.

### 🇵🇰 اردو — *یہ حصہ صرف آپ کے لیے ہے۔*

**یاد رکھنے والی باتیں:**

- یہ ایک فیصلہ سازی میں مدد دینے والا ٹول ہے، خودکار پائلٹ نہیں۔ ہر اسکرین آپ کی سمجھ بوجھ میں مدد دینے کے لیے بنائی گئی ہے، اُسے بدلنے کے لیے نہیں۔
- جب بھی ایپ میں کہیں "Not Available" (دستیاب نہیں) نظر آئے، یہ سافٹ ویئر کا آپ کے ساتھ ایماندار ہونا ہے — اسے مفید معلومات سمجھیں، کوئی خرابی نہیں۔
- سگنل پر موجود کانفیڈنس فیصد ایک امکان ہے، وعدہ نہیں۔ ایک اچھی وجہ رکھنے والی 85% کانفیڈنس ٹریڈ بھی نقصان دے سکتی ہے؛ کافی بڑے نمونے (sample) میں یہ عام اور متوقع بات ہے۔
- Trading Coach صرف اُتنا ہی اچھا ہے جتنا اُس کے پیچھے موجود ڈیٹا۔ اگر Evidence یا Research سیکشن کسی خاص اثاثے کے لیے خلا دکھائیں، تو Coach کے جواب کو بھی اُتنی ہی احتیاط سے دیکھیں۔
- Portfolio Intelligence کو باقاعدگی سے استعمال کریں، خاص طور پر کوریلیشن اور ایکسپوژر سیکشنز — بغیر جانے یہ محسوس کرنا آسان ہے کہ آپ متنوع (diversified) ہیں جبکہ حقیقت میں آپ کی کئی پوزیشنز ایک ساتھ حرکت کرتی ہوں۔

**سافٹ ویئر کے بارے میں کیسے سوچیں:**

اسے ایک بہت منظم، بہت ایماندار تحقیقاتی معاون سمجھیں — جو کبھی تھکتا نہیں، کبھی جذباتی نہیں ہوتا، اور ہمیشہ اپنی وجہ بتاتا ہے۔ یہ معلومات اکٹھی کرنے اور تجزیہ کرنے کا کام کرتا ہے۔ حتمی فیصلہ اب بھی آپ کرتے ہیں۔

**بچنے والی غلطیاں:**

- زیادہ کانفیڈنس اسکور کو یقین نہ سمجھیں — یہ اب بھی صرف ایک اندازہ ہے۔
- رسک/کوریلیشن وارننگز کو بند یا نظرانداز نہ کریں؛ یہ اس لیے موجود ہیں کیونکہ اس پروجیکٹ کے پرانے آڈٹس میں واقعی ایسی کمیاں ملی تھیں جن کے لیے بالکل یہی چیکس درکار تھے۔
- یہ مت سمجھیں کہ "AI" کا مطلب "غلطی سے پاک" ہے۔ اس پلیٹ فارم کا پورا ڈیزائن — ثبوت، وضاحتیں، ایماندار کمیاں — خاص طور پر اس لیے ہے کیونکہ AI کے نتائج کو جانچنا ضروری ہے، اندھا اعتماد کرنا نہیں۔
- اس پلیٹ فارم کو کبھی بھی اپنے کمپیوٹر کے علاوہ کہیں اور چلانے سے پہلے، پہلے سیکیورٹی چیک لسٹ پر عمل کریں (اصلی سیکیورٹی کی، لاگ اِن آن)۔ اگر یہ قدم چھوڑ دیا جائے تو پوری چیک لسٹ میں یہ واحد چیز ہے جس کے حقیقی نتائج ہو سکتے ہیں۔

---

## 10. Executive Summary

### 🇬🇧 English

**AI Crypto Signal Pro** is a personal, AI-assisted trading intelligence platform covering major cryptocurrencies, Gold/Silver/Oil, and smaller on-chain tokens. It was built to replace black-box signal services with a transparent system: every trade idea comes with a calculated confidence score, a full breakdown of the evidence behind it, supporting market context, and — on request — practical coaching on real trading decisions like when to enter, exit, or adjust a stop loss.

The platform is organized into clearly separated modules that each do one job and reuse each other's work rather than duplicating it: scanners gather market data, dashboards analyze it, an AI Decision Engine scores it, and dedicated Evidence, Research, and Coach modules explain and support — never override — that decision. Two more modules, Portfolio Intelligence and Performance Monitor, watch your actual holdings and historical results so you can track real risk and real accuracy over time.

The guiding principle throughout is honesty: no fabricated data, no invented AI opinions, and a clear "Not Available" whenever real information doesn't exist. The software does not predict the future, does not guarantee profit, and does not replace your own risk management — it exists to make your decisions better-informed, not automatic.

The project is currently in **Beta**. All planned modules are functionally complete and have passed a full project audit with zero blocking issues. What remains before Version 1.0 is a short, non-urgent checklist: confirming the newest desktop screens run correctly on your machine, a small cleanup decision, and (only if you ever deploy beyond your own computer) applying the security steps already prepared for that.

### 🇵🇰 اردو

**اے آئی کرپٹو سگنل پرو** ایک ذاتی، AI کی مدد سے چلنے والا ٹریڈنگ انٹیلی جنس پلیٹ فارم ہے جو بڑی کرپٹو کرنسیز، سونا/چاندی/تیل، اور چھوٹے آن چین ٹوکنز کو کور کرتا ہے۔ یہ بلیک باکس سگنل سروسز کی جگہ ایک شفاف نظام لانے کے لیے بنایا گیا: ہر ٹریڈ آئیڈیا کے ساتھ ایک حساب شدہ کانفیڈنس اسکور، اُس کے پیچھے موجود ثبوت کی مکمل تفصیل، معاون مارکیٹ تناظر، اور — درخواست پر — حقیقی ٹریڈنگ فیصلوں (جیسے کب اینٹری لینی ہے، کب نکلنا ہے، یا اسٹاپ لاس کب تبدیل کرنا ہے) پر عملی رہنمائی شامل ہوتی ہے۔

پلیٹ فارم واضح طور پر الگ الگ ماڈیولز میں تقسیم ہے، جن میں سے ہر ایک کا صرف ایک کام ہے اور یہ ایک دوسرے کا کام دوبارہ نہیں کرتے: اسکینرز مارکیٹ ڈیٹا اکٹھا کرتے ہیں، ڈیش بورڈز اُس کا تجزیہ کرتے ہیں، AI Decision Engine اُس کا اسکور نکالتا ہے، اور مخصوص Evidence، Research اور Coach ماڈیولز اُس فیصلے کی وضاحت اور معاونت کرتے ہیں — کبھی اُسے بدلتے نہیں۔ دو مزید ماڈیولز، Portfolio Intelligence اور Performance Monitor، آپ کی اصل ہولڈنگز اور پرانے نتائج پر نظر رکھتے ہیں تاکہ آپ وقت کے ساتھ اپنا حقیقی رسک اور حقیقی درستگی جان سکیں۔

پورے پلیٹ فارم کا رہنما اصول ایمانداری ہے: نہ کوئی بناوٹی ڈیٹا، نہ کوئی خودساختہ AI رائے، اور جہاں حقیقی معلومات موجود نہ ہو وہاں واضح "Not Available"۔ یہ سافٹ ویئر مستقبل کی پیشگوئی نہیں کرتا، منافع کی ضمانت نہیں دیتا، اور آپ کے اپنے رسک مینجمنٹ کی جگہ نہیں لیتا — یہ صرف آپ کے فیصلوں کو بہتر معلومات پر مبنی بنانے کے لیے موجود ہے، خودکار بنانے کے لیے نہیں۔

پروجیکٹ فی الحال **بیٹا** مرحلے میں ہے۔ تمام منصوبہ بند ماڈیولز فعالیت کے لحاظ سے مکمل ہیں اور مکمل پروجیکٹ آڈٹ سے گزر چکے ہیں جس میں کوئی رکاوٹ ڈالنے والا مسئلہ نہیں ملا۔ ورژن 1.0 سے پہلے صرف ایک مختصر، غیر فوری چیک لسٹ باقی ہے: اپنے کمپیوٹر پر نئی ڈیسک ٹاپ اسکرینز کی تصدیق کرنا، ایک چھوٹا صفائی کا فیصلہ، اور (صرف اگر آپ کبھی اپنے کمپیوٹر سے باہر ڈیپلائے کریں تو) پہلے سے تیار سیکیورٹی اقدامات لاگو کرنا۔

---

### 🇬🇧 What comes next / 🇵🇰 آگے کیا ہے

🇬🇧 *This document (01_Project_Overview.md) is the first in a planned documentation series. Later documents will go into more depth on specific areas — a full feature-by-feature user guide, the technical architecture behind each module, security and deployment procedures, and a glossary of terms used throughout the platform — without requiring any programming knowledge to read. Those documents have not been written yet and will follow in future sessions.*

🇵🇰 *یہ دستاویز (01_Project_Overview.md) ایک منصوبہ بند دستاویزی سلسلے کی پہلی کڑی ہے۔ بعد کی دستاویزات مخصوص شعبوں پر مزید تفصیل سے روشنی ڈالیں گی — ہر خصوصیت کی مکمل یوزر گائیڈ، ہر ماڈیول کے پیچھے تکنیکی ڈھانچہ، سیکیورٹی اور ڈیپلائمنٹ کے طریقہ کار، اور پلیٹ فارم میں استعمال ہونے والی اصطلاحات کی فرہنگ — ان سب کو پڑھنے کے لیے بھی کسی پروگرامنگ علم کی ضرورت نہیں ہوگی۔ یہ دستاویزات ابھی نہیں لکھی گئیں اور آئندہ سیشنز میں تیار کی جائیں گی۔*
