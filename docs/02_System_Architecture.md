# 02 — System Architecture

**AI Crypto Signal Pro**
**Document type:** Owner-facing architecture guide (non-technical) / مالک کے لیے فن تعمیر (architecture) کی رہنمائی
**Status:** Beta
**Last updated:** 2026-07-28

> 🇬🇧 This document teaches you how your software is *organized* — not how it is coded. Think of it as the floor plan of your house, not the bricklaying instructions. No programming knowledge is needed to read this.
>
> 🇵🇰 یہ دستاویز آپ کو یہ سکھاتی ہے کہ آپ کا سافٹ ویئر کس طرح *منظم* ہے — نہ کہ یہ کیسے کوڈ کیا گیا۔ اسے اپنے گھر کے نقشے کی طرح سمجھیں، اینٹیں لگانے کی ہدایات کی طرح نہیں۔ اسے پڑھنے کے لیے کسی پروگرامنگ علم کی ضرورت نہیں۔

---

## 1. What is Software Architecture?

### 🇬🇧 English

**Software architecture** is simply the plan for how all the different parts of a piece of software are organized, and how they are allowed to talk to each other. It is the same idea as a building's floor plan: a floor plan doesn't tell you what color to paint the walls, but it does tell you where the kitchen is, where the bedrooms are, and which rooms connect to which hallways.

**Why architecture matters:** without a plan, a growing piece of software turns into what people in the industry informally call a "tangled mess" — where every part touches every other part, and a small change in one place unexpectedly breaks something completely unrelated. Good architecture prevents this by giving every part of the software a clear, limited job.

**Why large software is divided into modules:** imagine running a company where every employee does a little bit of everything — a little accounting, a little sales, a little support — with no clear roles. It would be chaos, and mistakes would be everywhere. Instead, real companies have departments: Sales, Accounts, Customer Support, each with a clear job and clear handoffs between them. Your software is built the same way. Each part (called a **module**) has one job, does it well, and hands its results to the next module in line.

### 🇵🇰 اردو

**سافٹ ویئر آرکیٹیکچر** دراصل یہ منصوبہ ہے کہ سافٹ ویئر کے مختلف حصے کس طرح منظم ہیں، اور وہ آپس میں کیسے بات چیت کر سکتے ہیں۔ یہ بالکل ایک عمارت کے نقشے جیسا خیال ہے: نقشہ یہ نہیں بتاتا کہ دیواروں کو کس رنگ سے رنگنا ہے، لیکن یہ ضرور بتاتا ہے کہ کچن کہاں ہے، بیڈ روم کہاں ہیں، اور کون سا کمرہ کس راہداری سے جُڑا ہے۔

**آرکیٹیکچر کیوں اہم ہے:** بغیر منصوبے کے، بڑھتا ہوا سافٹ ویئر ایک ایسی الجھی ہوئی گتھی بن جاتا ہے جہاں ہر حصہ ہر دوسرے حصے کو چھوتا ہے، اور ایک جگہ کی چھوٹی سی تبدیلی اچانک کہیں اور کچھ بالکل غیر متعلقہ چیز کو خراب کر دیتی ہے۔ اچھا آرکیٹیکچر ہر حصے کو ایک واضح، محدود کام دے کر اس مسئلے کو روکتا ہے۔

**بڑے سافٹ ویئر کو ماڈیولز میں کیوں تقسیم کیا جاتا ہے:** تصور کریں کہ ایک کمپنی چلائی جا رہی ہے جہاں ہر ملازم تھوڑا تھوڑا سب کچھ کرتا ہے — تھوڑی اکاؤنٹنگ، تھوڑی سیلز، تھوڑی سپورٹ — بغیر کسی واضح ذمہ داری کے۔ یہ افراتفری ہو گی، اور غلطیاں ہر جگہ ہوں گی۔ اس کی بجائے، حقیقی کمپنیوں میں شعبے ہوتے ہیں: سیلز، اکاؤنٹس، کسٹمر سپورٹ، ہر ایک کا واضح کام اور آپس میں واضح حوالگی۔ آپ کا سافٹ ویئر بھی اسی طرح بنایا گیا ہے۔ ہر حصہ (جسے **ماڈیول** کہا جاتا ہے) کا ایک کام ہے، وہ اسے اچھی طرح کرتا ہے، اور اپنا نتیجہ اگلے ماڈیول کو دیتا ہے۔

### 💡 Owner Tip / مالک کے لیے مشورہ
🇬🇧 Whenever you're unsure about a change, ask: "which department (module) does this belong to?" If the answer isn't obvious, it's a sign to ask before touching it.
🇵🇰 جب بھی کسی تبدیلی کے بارے میں غیر یقینی ہوں، یہ پوچھیں: "یہ کس شعبے (ماڈیول) سے تعلق رکھتا ہے؟" اگر جواب واضح نہ ہو، تو یہ اس بات کا اشارہ ہے کہ چھونے سے پہلے پوچھ لیا جائے۔

### ⚠️ Common Mistake / عام غلطی
🇬🇧 Thinking "small software doesn't need architecture." Your software started small too — the reason it could grow to 9 major feature phases without falling apart is exactly *because* architecture was taken seriously from day one.
🇵🇰 یہ سوچنا کہ "چھوٹے سافٹ ویئر کو آرکیٹیکچر کی ضرورت نہیں۔" آپ کا سافٹ ویئر بھی چھوٹا شروع ہوا تھا — یہ 9 بڑے مراحل تک بغیر بکھرے بڑھ سکا، بالکل اسی لیے کیونکہ پہلے دن سے آرکیٹیکچر کو سنجیدگی سے لیا گیا۔

### 🧩 Real Example / حقیقی مثال
🇬🇧 In your platform, the "Trading Coach" module never recalculates a trading decision — it only reads what the "Decision Engine" module already decided. That boundary is architecture in action.
🇵🇰 آپ کے پلیٹ فارم میں "Trading Coach" ماڈیول کبھی بھی ٹریڈنگ فیصلہ دوبارہ نہیں نکالتا — یہ صرف وہ پڑھتا ہے جو "Decision Engine" ماڈیول پہلے ہی طے کر چکا ہے۔ یہ حد بندی ہی عملی آرکیٹیکچر ہے۔

### 🤔 Did You Know? / کیا آپ جانتے ہیں؟
🇬🇧 The word "architecture" in software was borrowed directly from building construction on purpose — the earliest software engineers wanted people to think of code the same disciplined way architects think about buildings.
🇵🇰 سافٹ ویئر میں لفظ "آرکیٹیکچر" جان بوجھ کر عمارتی تعمیر سے لیا گیا — ابتدائی سافٹ ویئر انجینئرز چاہتے تھے کہ لوگ کوڈ کے بارے میں بھی اُتنی ہی نظم و ضبط سے سوچیں جتنا معمار عمارتوں کے بارے میں سوچتے ہیں۔

### 📌 Chapter Summary / باب کا خلاصہ
🇬🇧 Architecture is the organizational plan behind your software. It divides work into modules (like company departments) so the system stays manageable, understandable, and safe to change as it grows.
🇵🇰 آرکیٹیکچر آپ کے سافٹ ویئر کے پیچھے کا تنظیمی منصوبہ ہے۔ یہ کام کو ماڈیولز (کمپنی کے شعبوں کی طرح) میں تقسیم کرتا ہے تاکہ سسٹم بڑھنے کے باوجود قابلِ انتظام، قابلِ فہم، اور محفوظ طریقے سے تبدیل کیا جا سکے۔

---

## 2. Overall System Architecture

### 🇬🇧 English

Your entire system is organized into layers, stacked on top of each other. Each layer only talks to the layer directly above or below it — it never reaches across and touches a layer far away. This is one of the most important rules in the whole system.

```
                 ┌───────────────────────────┐
                 │   Desktop Application      │   ← what you see and click (WPF)
                 │   (Views, Screens, Buttons) │
                 └──────────────┬──────────────┘
                                │  asks for data over the internet (locally)
                                ▼
                 ┌───────────────────────────┐
                 │        API Layer            │   ← the "front door" / receptionist
                 └──────────────┬──────────────┘
                                ▼
                 ┌───────────────────────────┐
                 │     Business Logic Layer    │   ← the Trading Agent's coordination
                 │  (Orchestrator, Coach, etc.) │
                 └──────────────┬──────────────┘
                                ▼
                 ┌───────────────────────────┐
                 │        AI Engine             │   ← scoring, calibration, SMC analysis
                 └──────────────┬──────────────┘
                                ▼
                 ┌───────────────────────────┐
                 │        Services Layer       │   ← Binance connection, portfolio math,
                 │                              │      position sizing, and more
                 └──────────────┬──────────────┘
                                ▼
                 ┌───────────────────────────┐
                 │      Repository Layer       │   ← the only part allowed to "speak database"
                 └──────────────┬──────────────┘
                                ▼
                 ┌───────────────────────────┐
                 │         Database             │   ← stores your signals & trade history
                 └──────────────┬──────────────┘
                                ▼
                 ┌───────────────────────────┐
                 │     External Providers      │   ← Binance, DexScreener, GoPlus, and others
                 └───────────────────────────┘
```

Each layer's job, in one line:

| Layer | One-line job |
|---|---|
| Desktop Application | Show you information, take your clicks |
| API Layer | Receive requests, send back answers |
| Business Logic Layer | Coordinate the whole trading-agent conversation |
| AI Engine | Turn market data into a scored decision |
| Services Layer | Do specific real-world jobs (talk to Binance, calculate risk, etc.) |
| Repository Layer | Read and write the database, safely |
| Database | Remember everything permanently |
| External Providers | Supply the real-world data none of this could exist without |

### 🇵🇰 اردو

آپ کا پورا سسٹم تہوں (layers) کی شکل میں منظم ہے، جو ایک دوسرے کے اوپر ترتیب سے رکھی گئی ہیں۔ ہر تہہ صرف اپنے بالکل اوپر یا نیچے والی تہہ سے بات کرتی ہے — یہ کبھی دور کی کسی تہہ کو براہ راست نہیں چھوتی۔ یہ پورے سسٹم کا سب سے اہم اصول ہے۔

| تہہ (Layer) | ایک لائن میں کام |
|---|---|
| ڈیسک ٹاپ ایپلیکیشن | آپ کو معلومات دکھانا، آپ کے کلکس لینا |
| API لیئر | درخواستیں وصول کرنا، جوابات واپس بھیجنا |
| بزنس لاجک لیئر | پورے ٹریڈنگ ایجنٹ کی بات چیت کو منظم کرنا |
| AI انجن | مارکیٹ ڈیٹا کو اسکور شدہ فیصلے میں بدلنا |
| سروسز لیئر | مخصوص حقیقی دنیا کے کام کرنا (بنانس سے بات، رسک کیلکولیشن وغیرہ) |
| ریپوزیٹری لیئر | ڈیٹابیس کو محفوظ طریقے سے پڑھنا اور لکھنا |
| ڈیٹابیس | ہر چیز کو مستقل طور پر یاد رکھنا |
| بیرونی فراہم کنندگان (External Providers) | وہ حقیقی دنیا کا ڈیٹا فراہم کرنا جس کے بغیر یہ سب کچھ ممکن نہیں |

### 💡 Owner Tip / مالک کے لیے مشورہ
🇬🇧 If someone ever suggests "let's make the Desktop App talk directly to the Database to save time," that's a red flag — it breaks the whole layered design and is exactly the kind of shortcut that causes long-term chaos.
🇵🇰 اگر کبھی کوئی تجویز دے کہ "چلیں وقت بچانے کے لیے ڈیسک ٹاپ ایپ کو براہ راست ڈیٹابیس سے جوڑ دیتے ہیں،" تو یہ ایک خطرے کی گھنٹی ہے — یہ پوری تہہ دار ڈیزائن کو توڑ دیتا ہے اور بالکل اُسی طرح کا شارٹ کٹ ہے جو طویل مدتی افراتفری پیدا کرتا ہے۔

### ⚠️ Common Mistake / عام غلطی
🇬🇧 Assuming more layers automatically means slower or more complicated software. In practice, this layering is what lets Portfolio Intelligence, Performance Monitor, and every other feature share the same reliable AI Engine instead of each building their own.
🇵🇰 یہ فرض کرنا کہ زیادہ تہیں خودکار طور پر سست یا زیادہ پیچیدہ سافٹ ویئر کا مطلب ہیں۔ عملی طور پر، یہی تہہ بندی Portfolio Intelligence، Performance Monitor، اور ہر دوسری خصوصیت کو ایک ہی قابلِ اعتماد AI Engine بانٹنے کے قابل بناتی ہے، بجائے اس کے کہ ہر ایک اپنا الگ انجن بنائے۔

### 🧩 Real Example / حقیقی مثال
🇬🇧 When you open the "AI Assistant" screen and ask a question, your click travels down through every layer in this diagram and the answer travels back up through every one of them — you just never see the layers, only the final answer.
🇵🇰 جب آپ "AI Assistant" اسکرین کھول کر کوئی سوال پوچھتے ہیں، تو آپ کا کلک اس نقشے کی ہر تہہ سے گزرتا ہوا نیچے جاتا ہے اور جواب واپس اُنہی تمام تہوں سے گزر کر اوپر آتا ہے — آپ کو صرف حتمی جواب نظر آتا ہے، تہیں کبھی نظر نہیں آتیں۔

### 🤔 Did You Know? / کیا آپ جانتے ہیں؟
🇬🇧 This style of design is called a "layered architecture," and it's one of the oldest, most trusted patterns in all of software engineering — banks, hospitals, and airlines use the exact same idea.
🇵🇰 اس طرز کے ڈیزائن کو "لیئرڈ آرکیٹیکچر" (layered architecture) کہا جاتا ہے، اور یہ سافٹ ویئر انجینئرنگ کے سب سے پرانے اور قابلِ اعتماد طریقوں میں سے ایک ہے — بینک، ہسپتال، اور ایئرلائنز بھی بالکل یہی خیال استعمال کرتے ہیں۔

### 📌 Chapter Summary / باب کا خلاصہ
🇬🇧 Your system is a stack of 8 layers, each with one job, each only talking to its direct neighbor. This is why the system stayed stable while growing to cover crypto, commodities, tokens, an AI agent, and portfolio tools.
🇵🇰 آپ کا سسٹم 8 تہوں کا ایک ڈھانچہ ہے، ہر ایک کا ایک کام ہے، اور ہر ایک صرف اپنی براہ راست ساتھی تہہ سے بات کرتی ہے۔ یہی وجہ ہے کہ کرپٹو، کموڈٹیز، ٹوکنز، AI ایجنٹ، اور پورٹ فولیو ٹولز تک بڑھنے کے باوجود سسٹم مستحکم رہا۔

---

## 3. Major Layers

### 🇬🇧 English

Each layer below has a clear purpose, a clear set of responsibilities, and clear inputs/outputs — it never guesses what another layer needs.

| Layer | Purpose | Responsibilities | Typical Input | Typical Output |
|---|---|---|---|---|
| **Desktop UI** | Let you see and interact with everything | Display screens, take clicks, show loading/error states | Your clicks and typed questions | Requests sent to the API Layer |
| **API** | The single "front door" for all requests | Receive a request, check it's valid, hand it to the right business logic, send back a response | A request from the desktop app | A structured answer (JSON data) |
| **Business Logic** | Coordinate multi-step processes | Decide which engines to call and in what order (e.g. for the Trading Agent: understand the question, call Decision Engine, call Evidence Engine, call Research Engine, maybe call Coach) | A parsed request/question | A combined, complete result |
| **AI Engine** | Turn analysis into a scored decision | Run the scoring formula, apply calibrated weights, produce confidence + recommendation | Technical/Smart Money/Security analysis | A confidence score + decision |
| **Services** | Do one specific real-world job each | Talk to Binance, calculate position size, compute portfolio risk, and so on | Raw data or a specific request | A specific, focused result |
| **Repository Layer** | The only place allowed to read/write the database | Fetch stored signals, save new trade outcomes, keep queries consistent | A request for stored data | Rows of real, stored history |
| **Database** | Remember everything permanently | Store every signal, every outcome, every setting | Data to save | Data to retrieve later |
| **External APIs** | Supply real-world data this software doesn't generate itself | Provide live prices, on-chain data, security checks, sentiment data | A request for real data | Real, live information |

### 🇵🇰 اردو

نیچے دی گئی ہر تہہ کا ایک واضح مقصد، واضح ذمہ داریاں، اور واضح ان پُٹ/آؤٹ پُٹ ہے — یہ کبھی اندازہ نہیں لگاتی کہ دوسری تہہ کو کیا چاہیے۔

| تہہ | مقصد | ذمہ داریاں | عام ان پُٹ | عام آؤٹ پُٹ |
|---|---|---|---|---|
| **ڈیسک ٹاپ UI** | آپ کو ہر چیز دیکھنے اور استعمال کرنے دینا | اسکرینز دکھانا، کلکس لینا، لوڈنگ/ایرر حالتیں دکھانا | آپ کے کلکس اور لکھے گئے سوالات | API لیئر کو بھیجی گئی درخواستیں |
| **API** | تمام درخواستوں کا واحد "اگلا دروازہ" | درخواست وصول کرنا، اُس کی جانچ کرنا، صحیح بزنس لاجک کو دینا، جواب واپس بھیجنا | ڈیسک ٹاپ ایپ سے درخواست | ایک منظم جواب (JSON ڈیٹا) |
| **بزنس لاجک** | کئی مراحل کے عمل کو منظم کرنا | یہ طے کرنا کہ کون سے انجن کب بلانے ہیں (مثلاً Trading Agent کے لیے: سوال سمجھنا، Decision Engine بلانا، Evidence Engine بلانا، Research Engine بلانا، شاید Coach بلانا) | سمجھی گئی درخواست/سوال | ایک مکمل، ملا ہوا نتیجہ |
| **AI انجن** | تجزیے کو اسکور شدہ فیصلے میں بدلنا | اسکورنگ فارمولا چلانا، کیلیبریٹڈ وزن لگانا، کانفیڈنس + سفارش نکالنا | ٹیکنیکل/سمارٹ منی/سیکیورٹی تجزیہ | کانفیڈنس اسکور + فیصلہ |
| **سروسز** | ہر ایک مخصوص حقیقی دنیا کا کام کرنا | بنانس سے بات کرنا، پوزیشن سائز نکالنا، پورٹ فولیو رسک حساب کرنا وغیرہ | خام ڈیٹا یا مخصوص درخواست | ایک مخصوص، واضح نتیجہ |
| **ریپوزیٹری لیئر** | ڈیٹابیس پڑھنے/لکھنے کی واحد جگہ | محفوظ سگنلز نکالنا، نئے ٹریڈ نتائج محفوظ کرنا، سوالات کو یکساں رکھنا | محفوظ ڈیٹا کی درخواست | حقیقی، محفوظ ہسٹری کی قطاریں |
| **ڈیٹابیس** | ہر چیز مستقل طور پر یاد رکھنا | ہر سگنل، ہر نتیجہ، ہر سیٹنگ محفوظ کرنا | محفوظ کرنے کے لیے ڈیٹا | بعد میں حاصل کرنے کے لیے ڈیٹا |
| **بیرونی APIs** | حقیقی دنیا کا ڈیٹا فراہم کرنا جو یہ سافٹ ویئر خود نہیں بناتا | لائیو قیمتیں، آن چین ڈیٹا، سیکیورٹی چیکس، جذباتی ڈیٹا دینا | حقیقی ڈیٹا کی درخواست | حقیقی، لائیو معلومات |

### 💡 Owner Tip / مالک کے لیے مشورہ
🇬🇧 If a feature request sounds like "just make the Desktop App do X directly," always ask which layer should actually own that job. There usually already is one.
🇵🇰 اگر کوئی خصوصیت کی درخواست ایسی لگے کہ "بس ڈیسک ٹاپ ایپ کو یہ کام براہ راست کرنے دیں،" تو ہمیشہ پوچھیں کہ یہ کام دراصل کس تہہ کا ہونا چاہیے۔ عام طور پر پہلے سے ایک موجود ہوتی ہے۔

### ⚠️ Common Mistake / عام غلطی
🇬🇧 Confusing the "Services Layer" with the "AI Engine." Services do practical jobs (talk to Binance, do math); the AI Engine specifically produces scored trading decisions. Mixing these up was exactly what your project's Phase 7 rule ("do not create a second scoring engine") was written to prevent.
🇵🇰 "سروسز لیئر" کو "AI انجن" کے ساتھ خلط ملط کرنا۔ سروسز عملی کام کرتی ہیں (بنانس سے بات، حساب کتاب)؛ AI انجن خاص طور پر اسکور شدہ ٹریڈنگ فیصلے بناتا ہے۔ ان دونوں کو ملا دینا بالکل وہی چیز تھی جسے روکنے کے لیے آپ کے پروجیکٹ کے Phase 7 کے اصول ("دوسرا اسکورنگ انجن نہ بنانا") لکھا گیا تھا۔

### 🧩 Real Example / حقیقی مثال
🇬🇧 Portfolio Intelligence is a Service — it doesn't invent new scores. For each position, it *calls* the AI Engine's existing Decision Engine to get a fresh read, then does its own separate job (weighting, correlation math). That's a Service correctly using the AI Engine, not replacing it.
🇵🇰 Portfolio Intelligence ایک سروس ہے — یہ نئے اسکور نہیں بناتی۔ ہر پوزیشن کے لیے، یہ AI انجن کے موجودہ Decision Engine کو *بُلاتی* ہے تاکہ تازہ ریڈنگ ملے، پھر اپنا الگ کام کرتی ہے (وزن، کوریلیشن حساب)۔ یہ ایک سروس کا AI انجن کو درست طریقے سے استعمال کرنا ہے، اسے بدلنا نہیں۔

### 🤔 Did You Know? / کیا آپ جانتے ہیں؟
🇬🇧 The Repository Layer exists specifically so that only ONE part of the whole system knows how to "speak database." Every other layer just asks the Repository Layer politely — this means the database itself could change someday without every other layer needing to know or care.
🇵🇰 ریپوزیٹری لیئر خاص طور پر اس لیے موجود ہے تاکہ پورے سسٹم کا صرف ایک حصہ ہی "ڈیٹابیس کی زبان" جانتا ہو۔ ہر دوسری تہہ صرف ریپوزیٹری لیئر سے شائستگی سے پوچھتی ہے — اس کا مطلب یہ ہے کہ اگر کبھی ڈیٹابیس خود بدلا جائے، تو باقی تہوں کو اس کی فکر کرنے کی ضرورت نہیں۔

### 📌 Chapter Summary / باب کا خلاصہ
🇬🇧 Eight layers, eight clear jobs. Each one has defined inputs and outputs, and none of them try to do another layer's work.
🇵🇰 آٹھ تہیں، آٹھ واضح کام۔ ہر ایک کے متعین ان پُٹ اور آؤٹ پُٹ ہیں، اور کوئی بھی دوسری تہہ کا کام کرنے کی کوشش نہیں کرتی۔

---

## 4. Project Folder Structure

### 🇬🇧 English

Your backend code (the part that runs all the analysis and remembers your data) is organized into folders, and each folder matches one of the layers you just learned about. You never need to open these folders yourself — this is here so that if you ever discuss the project with anyone, you'll understand what they mean.

```
app/
├── ai/            → The AI scoring formula and its calibration
├── agent/         → The Trading Agent's "brain" - coordinates Decision, Evidence,
│                    Research, and Coach together
├── api/           → The "front door" - every request from the desktop app arrives here
├── core/          → Shared foundational settings (configuration, security, logging)
├── models/        → The permanent shape of your data (what a "Signal" or "Coin" is)
├── repositories/  → The only folder allowed to talk to the database directly
├── schemas/       → The agreed "shape" of data sent between the app and the backend
├── services/      → Specific real-world jobs (Binance connection, risk math, and more)
├── smc/           → Smart Money Concepts detection (structure breaks, liquidity, zones)
├── scheduler/      → The always-running background scanner and signal monitor
├── websocket/      → Real-time push updates to the desktop app
├── security/       → Encryption for your saved exchange API keys
└── backtest/       → Testing trading logic against past market history
```

Your desktop application (what you actually see and click) has its own, separate organization:

```
AI_Crypto_Signal_Pro/
├── Views/         → Every screen you see (Dashboard, Token Scanner, AI Assistant, etc.)
├── ViewModels/     → The "memory" behind each screen - holds what's currently shown
├── Services/       → How the desktop app talks to the backend (API calls, live updates)
├── Models/         → The desktop app's own copy of "what data looks like"
└── Converters/     → Small helpers that translate raw data into what you see on screen
```

### 🇵🇰 اردو

آپ کا بیک اینڈ کوڈ (وہ حصہ جو تمام تجزیہ چلاتا ہے اور آپ کا ڈیٹا یاد رکھتا ہے) فولڈرز میں منظم ہے، اور ہر فولڈر اُن تہوں میں سے کسی ایک سے مماثلت رکھتا ہے جو آپ نے ابھی سیکھیں۔ آپ کو خود یہ فولڈرز کھولنے کی کبھی ضرورت نہیں — یہ صرف اس لیے یہاں ہے تاکہ اگر کبھی آپ کسی سے پروجیکٹ پر بات کریں، تو آپ سمجھ سکیں کہ اُن کا کیا مطلب ہے۔

| فولڈر | ذمہ داری |
|---|---|
| `ai/` | AI اسکورنگ فارمولا اور اس کی کیلیبریشن |
| `agent/` | Trading Agent کا "دماغ" - Decision، Evidence، Research، اور Coach کو ملا کر چلاتا ہے |
| `api/` | "اگلا دروازہ" - ڈیسک ٹاپ ایپ کی ہر درخواست یہاں پہنچتی ہے |
| `core/` | مشترکہ بنیادی سیٹنگز (کنفیگریشن، سیکیورٹی، لاگنگ) |
| `models/` | آپ کے ڈیٹا کی مستقل شکل (جیسے "Signal" یا "Coin" کیا ہے) |
| `repositories/` | واحد فولڈر جسے براہ راست ڈیٹابیس سے بات کرنے کی اجازت ہے |
| `schemas/` | ایپ اور بیک اینڈ کے درمیان بھیجے جانے والے ڈیٹا کی طے شدہ "شکل" |
| `services/` | مخصوص حقیقی دنیا کے کام (بنانس کنکشن، رسک حساب وغیرہ) |
| `smc/` | Smart Money Concepts کی شناخت (اسٹرکچر بریکس، لیکویڈیٹی، زونز) |
| `scheduler/` | ہمیشہ چلنے والا بیک گراؤنڈ اسکینر اور سگنل مانیٹر |
| `websocket/` | ڈیسک ٹاپ ایپ کو ریئل ٹائم اپڈیٹس بھیجنا |
| `security/` | آپ کی محفوظ کردہ ایکسچینج API کیز کی خفیہ کاری (encryption) |
| `backtest/` | ٹریڈنگ منطق کو ماضی کی مارکیٹ ہسٹری کے خلاف آزمانا |

ڈیسک ٹاپ ایپلیکیشن (جو آپ حقیقت میں دیکھتے اور کلک کرتے ہیں) کا اپنا الگ نظم ہے: `Views/` (ہر اسکرین)، `ViewModels/` (ہر اسکرین کی "یادداشت")، `Services/` (بیک اینڈ سے بات چیت)، `Models/` (ڈیٹا کی شکل کی اپنی کاپی)، اور `Converters/` (خام ڈیٹا کو اسکرین کی شکل میں بدلنے والے چھوٹے مددگار)۔

### 💡 Owner Tip / مالک کے لیے مشورہ
🇬🇧 If you ever hire help or discuss the project with a developer, this folder map is the fastest way to ask "where does X live?" without needing to read a single line of code yourself.
🇵🇰 اگر آپ کبھی کسی کی مدد لیں یا کسی ڈیویلپر سے پروجیکٹ پر بات کریں، تو یہ فولڈر نقشہ سب سے تیز طریقہ ہے یہ پوچھنے کا کہ "X کہاں موجود ہے؟" بغیر خود کوڈ کی ایک لائن بھی پڑھے۔

### ⚠️ Common Mistake / عام غلطی
🇬🇧 Assuming the folder names describe *screens*. They describe *responsibilities* — for example, `services/` has nothing to do with what you see on screen; it's pure background work.
🇵🇰 یہ فرض کرنا کہ فولڈر کے نام *اسکرینوں* کو بیان کرتے ہیں۔ یہ دراصل *ذمہ داریوں* کو بیان کرتے ہیں — مثال کے طور پر، `services/` کا اسکرین پر نظر آنے والی چیز سے کوئی تعلق نہیں؛ یہ خالص بیک گراؤنڈ کام ہے۔

### 🧩 Real Example / حقیقی مثال
🇬🇧 When the AI Trading Coach was added, no existing folder was gutted or rewritten — a focused addition was made inside `agent/`, because that's exactly where "coordinating trading conversations" already lived.
🇵🇰 جب AI Trading Coach شامل کیا گیا، تو کوئی موجودہ فولڈر نہ ہٹایا گیا نہ دوبارہ لکھا گیا — `agent/` کے اندر ایک واضح اضافہ کیا گیا، کیونکہ "ٹریڈنگ گفتگو کو منظم کرنا" پہلے سے بالکل یہیں موجود تھا۔

### 🤔 Did You Know? / کیا آپ جانتے ہیں؟
🇬🇧 The `smc/` folder is named after "Smart Money Concepts" — a well-known school of technical analysis used by professional traders, not something invented specifically for this project.
🇵🇰 `smc/` فولڈر کا نام "Smart Money Concepts" کے نام پر ہے — یہ پیشہ ور ٹریڈرز کا ایک معروف ٹیکنیکل تجزیے کا طریقہ ہے، یہ خاص طور پر اس پروجیکٹ کے لیے ایجاد نہیں کیا گیا۔

### 📌 Chapter Summary / باب کا خلاصہ
🇬🇧 Each folder matches one clear responsibility, mirroring the layers from Section 3. Nothing is scattered randomly.
🇵🇰 ہر فولڈر ایک واضح ذمہ داری سے مطابقت رکھتا ہے، بالکل سیکشن 3 کی تہوں کی طرح۔ کچھ بھی بے ترتیبی سے نہیں بکھرا ہوا۔

---

## 5. AI Architecture

### 🇬🇧 English

The AI side of your platform is itself organized as a chain of specialized stages — no single giant "AI" does everything. Here is the chain, and why each link exists:

```
      Scanner
         │   finds which assets to look at right now
         ▼
        SMC
         │   reads market structure like a professional price-action trader
         ▼
    Indicators
         │   adds classic technical signals (trend, momentum, volatility)
         ▼
    AI Scoring
         │   combines everything into one calibrated confidence number
         ▼
  Decision Engine
         │   turns the score into a clear final answer: entry, stop, target
         ▼
      Evidence
         │   explains WHY, using only what already happened above
         ▼
      Research
         │   adds broader market context, never a signal itself
         ▼
   Trading Coach
         │   answers your practical "what should I do right now" questions
         ▼
        You
```

**Why every stage exists separately, instead of one big "AI":**

| Stage | Why it's separate |
|---|---|
| Scanner | Finding candidates is a different job from analyzing them — mixing the two would slow both down |
| SMC | Structure-reading is a specialized skill; keeping it separate means it can be improved without touching scoring |
| Indicators | Classic indicators are well-understood and testable on their own |
| AI Scoring | The actual "judgment" step - this is the only place numbers get combined into a score |
| Decision Engine | Turns a score into a real, tradeable plan (entry/stop/target) - a distinct translation step |
| Evidence | Explaining is a completely different skill from deciding - keeping it separate is what guarantees it can never quietly change the decision |
| Research | Context is not a signal - keeping it in its own stage stops it from ever being confused with one |
| Trading Coach | Practical Q&A is a different need from raw analysis - and by being last in the chain, it can never skip past the real decision |

### 🇵🇰 اردو

آپ کے پلیٹ فارم کا AI حصہ خود بھی مخصوص مراحل کے ایک سلسلے کی صورت میں منظم ہے — کوئی ایک بڑا "AI" سب کچھ نہیں کرتا۔ یہ رہا وہ سلسلہ، اور ہر کڑی کیوں موجود ہے:

| مرحلہ | کیوں الگ ہے |
|---|---|
| اسکینر (Scanner) | امیدواروں کو تلاش کرنا اُنہیں تجزیہ کرنے سے مختلف کام ہے — دونوں کو ملانا دونوں کو سست کر دے گا |
| SMC | ساخت پڑھنا ایک خصوصی مہارت ہے؛ اسے الگ رکھنے سے اسے اسکورنگ کو چھیڑے بغیر بہتر بنایا جا سکتا ہے |
| انڈیکیٹرز (Indicators) | روایتی انڈیکیٹرز اچھی طرح سمجھے گئے ہیں اور خود سے قابلِ آزمائش ہیں |
| AI اسکورنگ | اصل "فیصلہ سازی" کا مرحلہ - یہی واحد جگہ ہے جہاں نمبروں کو ملا کر اسکور بنایا جاتا ہے |
| Decision Engine | اسکور کو ایک حقیقی، قابلِ ٹریڈ منصوبے (اینٹری/اسٹاپ/ٹارگٹ) میں بدلتا ہے - ایک الگ تبدیلی کا مرحلہ |
| Evidence | وضاحت کرنا فیصلہ کرنے سے بالکل مختلف مہارت ہے - اسے الگ رکھنا اس بات کی ضمانت دیتا ہے کہ یہ کبھی خاموشی سے فیصلہ نہیں بدل سکتا |
| Research | تناظر کوئی سگنل نہیں - اسے اپنے الگ مرحلے میں رکھنا اسے کبھی سگنل سمجھے جانے سے روکتا ہے |
| Trading Coach | عملی سوال و جواب خام تجزیے سے مختلف ضرورت ہے - اور سلسلے میں آخر میں ہونے کی وجہ سے، یہ کبھی حقیقی فیصلے کو نظرانداز نہیں کر سکتا |

### 💡 Owner Tip / مالک کے لیے مشورہ
🇬🇧 If you ever want to check "is the AI being honest," the Evidence Engine is the place to look — it's specifically designed to be the transparency layer for everything the AI Scoring stage decided.
🇵🇰 اگر آپ کبھی چیک کرنا چاہیں کہ "کیا AI ایماندار ہے،" تو Evidence Engine وہ جگہ ہے جہاں دیکھنا چاہیے — یہ خاص طور پر AI Scoring مرحلے کے ہر فیصلے کی شفافیت کے لیے بنایا گیا ہے۔

### ⚠️ Common Mistake / عام غلطی
🇬🇧 Thinking the Trading Coach "thinks for itself." It doesn't — it is architecturally forbidden from producing an opinion the Decision Engine didn't already support. This is a deliberate safety boundary, not a limitation to work around.
🇵🇰 یہ سوچنا کہ Trading Coach "خود سے سوچتا ہے۔" ایسا نہیں — یہ آرکیٹیکچرل طور پر ایسی کوئی رائے دینے سے منع ہے جسے Decision Engine پہلے سے سپورٹ نہ کرے۔ یہ ایک جان بوجھ کر بنائی گئی حفاظتی حد ہے، کوئی خامی نہیں جسے بائی پاس کرنا ہے۔

### 🧩 Real Example / حقیقی مثال
🇬🇧 When you ask the Trading Coach "should I move my stop loss?", it does not go re-analyze the chart. It reads the Decision Engine's existing stop-loss logic and the Evidence Engine's existing reasoning, and answers from that alone.
🇵🇰 جب آپ Trading Coach سے پوچھتے ہیں "کیا مجھے اسٹاپ لاس منتقل کرنا چاہیے؟"، یہ چارٹ کا دوبارہ تجزیہ نہیں کرتا۔ یہ Decision Engine کی موجودہ اسٹاپ لاس منطق اور Evidence Engine کی موجودہ وجوہات پڑھتا ہے، اور صرف اُسی سے جواب دیتا ہے۔

### 🤔 Did You Know? / کیا آپ جانتے ہیں؟
🇬🇧 The AI Scoring weights are not fixed forever — they are recalibrated automatically using your own real trade history, separately for crypto, gold, silver, and oil, because each market genuinely behaves differently.
🇵🇰 AI Scoring کے وزن (weights) ہمیشہ کے لیے مقرر نہیں — انہیں آپ کی اپنی حقیقی ٹریڈ ہسٹری کے ذریعے خودکار طور پر دوبارہ کیلیبریٹ کیا جاتا ہے، کرپٹو، سونا، چاندی، اور تیل کے لیے الگ الگ، کیونکہ ہر مارکیٹ واقعی مختلف انداز میں چلتی ہے۔

### 📌 Chapter Summary / باب کا خلاصہ
🇬🇧 The AI is a disciplined 8-stage chain, not one black box. Each stage has one job, and the order guarantees explanation always follows decision — never the other way around.
🇵🇰 AI ایک منظم 8 مرحلوں کا سلسلہ ہے، کوئی ایک بلیک باکس نہیں۔ ہر مرحلے کا ایک کام ہے، اور ترتیب اس بات کی ضمانت دیتی ہے کہ وضاحت ہمیشہ فیصلے کے بعد آئے — کبھی الٹا نہیں۔

---

## 6. Data Flow

### 🇬🇧 English

This section shows how information physically travels through the system for a single request — from the moment you do something to the moment you see a result.

```
        You
         │  click a button / type a question
         ▼
      Scanner
         │  gathers live market data for the relevant asset(s)
         ▼
      Analysis
         │  Technical + Smart Money + (Security, for tokens) dashboards run
         ▼
         AI
         │  Decision Engine scores it; Evidence/Research/Coach add explanation
         ▼
       Result
         │  a complete, structured answer travels back up through every layer
         ▼
    Desktop UI
         │  formatted into the screen you actually see
         ▼
        You
```

The important thing to notice: **data only ever flows in one direction at a time, through defined stops.** It never skips a stage, and no stage secretly reaches ahead to grab something it isn't supposed to have yet.

### 🇵🇰 اردو

یہ سیکشن دکھاتا ہے کہ ایک درخواست کے لیے معلومات سسٹم میں جسمانی طور پر کیسے سفر کرتی ہیں — جس لمحے آپ کچھ کرتے ہیں سے لے کر جس لمحے آپ نتیجہ دیکھتے ہیں تک۔

قابلِ توجہ اہم بات: **ڈیٹا ہمیشہ ایک وقت میں ایک ہی سمت میں، متعین رُکاوٹوں سے گزر کر بہتا ہے۔** یہ کبھی کوئی مرحلہ نہیں چھوڑتا، اور کوئی مرحلہ خفیہ طور پر آگے بڑھ کر ایسی چیز نہیں لیتا جو ابھی اُس کے پاس نہیں ہونی چاہیے۔

قدم بہ قدم: آپ کچھ کرتے ہیں ← Scanner لائیو مارکیٹ ڈیٹا اکٹھا کرتا ہے ← Analysis (ٹیکنیکل + سمارٹ منی + سیکیورٹی) چلتا ہے ← AI اسکور کرتا ہے اور Evidence/Research/Coach وضاحت شامل کرتے ہیں ← نتیجہ واپس تمام تہوں سے گزر کر اوپر آتا ہے ← ڈیسک ٹاپ UI اسے اسکرین کی شکل دیتا ہے ← آپ دیکھتے ہیں۔

### 💡 Owner Tip / مالک کے لیے مشورہ
🇬🇧 If a screen ever feels "slow," it's usually because one specific stage in this chain (often a live external data call) is taking time — not because the whole system is broken.
🇵🇰 اگر کبھی کوئی اسکرین "سست" محسوس ہو، تو عام طور پر اس چین کا کوئی ایک مخصوص مرحلہ (اکثر لائیو بیرونی ڈیٹا کال) وقت لے رہا ہوتا ہے — نہ کہ پورا سسٹم خراب ہو۔

### ⚠️ Common Mistake / عام غلطی
🇬🇧 Expecting every screen to follow this exact full chain. Simpler screens (like checking your account balance) use a much shorter path — this diagram describes the *richest* case, like the AI Assistant.
🇵🇰 ہر اسکرین سے اس مکمل چین پر عمل کرنے کی توقع رکھنا۔ سادہ اسکرینز (جیسے اکاؤنٹ بیلنس چیک کرنا) بہت مختصر راستہ استعمال کرتی ہیں — یہ خاکہ سب سے *مکمل* صورتحال بیان کرتا ہے، جیسے AI Assistant۔

### 🧩 Real Example / حقیقی مثال
🇬🇧 Asking "Analyze BTCUSDT" in the AI Assistant travels the full path in this diagram. Just opening the Dashboard screen to see your account balance takes a much shorter route.
🇵🇰 AI Assistant میں "Analyze BTCUSDT" پوچھنا اس خاکے کا مکمل راستہ طے کرتا ہے۔ صرف Dashboard اسکرین کھول کر اکاؤنٹ بیلنس دیکھنا بہت مختصر راستہ لیتا ہے۔

### 🤔 Did You Know? / کیا آپ جانتے ہیں؟
🇬🇧 Every one of these steps typically completes in under a couple of seconds, even though real data is being fetched from multiple outside sources each time.
🇵🇰 ان میں سے ہر قدم عام طور پر چند سیکنڈ سے کم وقت میں مکمل ہو جاتا ہے، حالانکہ ہر بار کئی بیرونی ذرائع سے حقیقی ڈیٹا حاصل کیا جا رہا ہوتا ہے۔

### 📌 Chapter Summary / باب کا خلاصہ
🇬🇧 Data flows in one clear direction through defined stops - nothing is skipped, nothing is guessed.
🇵🇰 ڈیٹا ایک واضح سمت میں، متعین رکاوٹوں سے گزر کر بہتا ہے - کچھ بھی چھوڑا نہیں جاتا، کچھ بھی اندازے سے نہیں کیا جاتا۔

---

## 7. Module Relationships

### 🇬🇧 English

Some modules depend on others — meaning they read that module's output rather than doing the work themselves. Here is the real dependency map:

```
              Decision Engine
             /      │      \      \
            /       │       \      \
     Evidence   Research   Trading  Portfolio
      Engine      Engine    Coach   Intelligence
                                        │
                                   (reads Decision Engine
                                    fresh, per position)

     Performance Monitor  ──depends on──►  stored Signal History
                                            + Calibration data

     Market Scanner  ──shares──►  Technical Dashboard  ◄──shares──  Token Scanner
                                  (same engine, used by both)
```

**In plain words:**

- Evidence Engine, Research Engine, and Trading Coach all depend on the Decision Engine — none of them ever compute their own trading decision.
- Portfolio Intelligence depends on the Decision Engine too, but calls it fresh for each position you hold, rather than storing a separate copy of the logic.
- Performance Monitor depends only on stored history and the Calibration system — it never touches live analysis.
- Market Scanner and Token Scanner are two different front doors that both lead to the *same* Technical Dashboard engine — this is intentional, so a chart-reading improvement helps both at once.

**Why duplicate logic was avoided:** if the Decision Engine's scoring logic existed in two places, they could quietly drift apart over time — one might get fixed or improved and the other forgotten, and you'd never know which answer to trust. By having exactly one Decision Engine that everything else reads from, there is only ever one "truth" in the whole system.

### 🇵🇰 اردو

کچھ ماڈیولز دوسروں پر منحصر (depend) ہیں — یعنی وہ خود کام کرنے کی بجائے اُس ماڈیول کا نتیجہ پڑھتے ہیں۔ یہ رہا اصل انحصار کا نقشہ:

**سادہ الفاظ میں:**

- Evidence Engine، Research Engine، اور Trading Coach سب کا انحصار Decision Engine پر ہے — ان میں سے کوئی بھی کبھی اپنا ٹریڈنگ فیصلہ خود نہیں نکالتا۔
- Portfolio Intelligence کا انحصار بھی Decision Engine پر ہے، لیکن یہ آپ کی ہر پوزیشن کے لیے اسے تازہ طریقے سے بلاتا ہے، منطق کی الگ کاپی محفوظ کرنے کی بجائے۔
- Performance Monitor کا انحصار صرف محفوظ ہسٹری اور Calibration سسٹم پر ہے — یہ کبھی لائیو تجزیے کو نہیں چھوتا۔
- Market Scanner اور Token Scanner دو مختلف اگلے دروازے ہیں جو دونوں *ایک ہی* Technical Dashboard انجن تک جاتے ہیں — یہ جان بوجھ کر ایسا کیا گیا ہے، تاکہ چارٹ پڑھنے میں کوئی بہتری دونوں کو ایک ساتھ فائدہ دے۔

**دہری منطق (duplicate logic) سے کیوں بچا گیا:** اگر Decision Engine کی اسکورنگ منطق دو جگہوں پر موجود ہوتی، تو وہ وقت کے ساتھ خاموشی سے ایک دوسرے سے مختلف ہو سکتی تھیں — ایک کو شاید ٹھیک یا بہتر کر دیا جائے اور دوسری کو بھول جایا جائے، اور آپ کو کبھی معلوم نہ ہو کہ کس جواب پر بھروسہ کرنا ہے۔ صرف ایک ہی Decision Engine رکھنے سے، جسے باقی سب پڑھتے ہیں، پورے سسٹم میں ہمیشہ صرف ایک ہی "سچائی" ہوتی ہے۔

### 💡 Owner Tip / مالک کے لیے مشورہ
🇬🇧 Whenever someone proposes a new feature, the first architecture question should always be: "can this reuse the Decision Engine, or does it genuinely need something new?" Every phase of this project has answered that question before writing anything.
🇵🇰 جب بھی کوئی نئی خصوصیت تجویز کی جائے، سب سے پہلا آرکیٹیکچر سوال ہمیشہ یہ ہونا چاہیے: "کیا یہ Decision Engine کو دوبارہ استعمال کر سکتی ہے، یا اسے واقعی کسی نئی چیز کی ضرورت ہے؟" اس پروجیکٹ کے ہر مرحلے نے کچھ لکھنے سے پہلے اس سوال کا جواب دیا ہے۔

### ⚠️ Common Mistake / عام غلطی
🇬🇧 Assuming "reused" means "less powerful" or "cheaper." Reuse here means *more* trustworthy — every module that reuses the Decision Engine benefits from every improvement ever made to it.
🇵🇰 یہ فرض کرنا کہ "دوبارہ استعمال شدہ" کا مطلب "کم طاقتور" یا "سستا" ہے۔ یہاں دوبارہ استعمال کا مطلب *زیادہ* قابلِ اعتماد ہونا ہے — ہر ماڈیول جو Decision Engine دوبارہ استعمال کرتا ہے، اُس میں کی گئی ہر بہتری سے فائدہ اٹھاتا ہے۔

### 🧩 Real Example / حقیقی مثال
🇬🇧 Portfolio Intelligence didn't need its own risk-scoring system built from scratch — it simply calls the same Decision Engine that already scores every signal, once per held position.
🇵🇰 Portfolio Intelligence کو اپنا الگ رسک اسکورنگ سسٹم شروع سے بنانے کی ضرورت نہیں پڑی — یہ صرف اُسی Decision Engine کو بلاتا ہے جو پہلے سے ہر سگنل کو اسکور کرتا ہے، ہر رکھی گئی پوزیشن کے لیے ایک بار۔

### 🤔 Did You Know? / کیا آپ جانتے ہیں؟
🇬🇧 This "one shared brain, many modules reading from it" pattern has a formal name in software design: "single source of truth." It's considered one of the most valuable architecture principles in professional software.
🇵🇰 "ایک مشترکہ دماغ، بہت سے ماڈیولز اُس سے پڑھتے ہیں" والے اس طریقے کا سافٹ ویئر ڈیزائن میں ایک باقاعدہ نام ہے: "سنگل سورس آف ٹروتھ" (single source of truth)۔ اسے پیشہ ور سافٹ ویئر کے سب سے قیمتی آرکیٹیکچر اصولوں میں سے ایک سمجھا جاتا ہے۔

### 📌 Chapter Summary / باب کا خلاصہ
🇬🇧 Almost every advanced module in your platform depends on the same Decision Engine rather than duplicating it - this is deliberate, and it's what keeps the whole system trustworthy and consistent.
🇵🇰 آپ کے پلیٹ فارم کا تقریباً ہر جدید ماڈیول اُسی ایک Decision Engine پر منحصر ہے، اُسے دہرانے کی بجائے - یہ جان بوجھ کر کیا گیا ہے، اور یہی پورے سسٹم کو قابلِ بھروسہ اور یکساں رکھتا ہے۔

---

## 8. Why This Architecture Was Chosen

### 🇬🇧 English

| Advantage | What it means in practice |
|---|---|
| **Easy maintenance** | Fixing a problem in one module doesn't risk breaking an unrelated one |
| **Easy testing** | Each module can be checked on its own before being trusted with real work |
| **Easy upgrades** | The AI Scoring formula can be recalibrated without touching the Desktop App at all |
| **Easy debugging** | When something goes wrong, the layered structure narrows down *where* to look |
| **Reusable modules** | The Decision Engine has been reused by 5+ different features without being rewritten once |
| **Future expansion** | New features (like Portfolio Intelligence and AI Performance) were added as *additions*, never as rewrites of what already worked |

This isn't theoretical — it's exactly what happened. Your platform grew from a single Market Scanner to a full suite covering commodities, on-chain tokens, an AI conversational agent, portfolio analytics, and performance tracking, across many development phases, **without ever having to throw away and rebuild an earlier feature.** That is the direct, measurable payoff of this architecture.

### 🇵🇰 اردو

| فائدہ | عملی مطلب |
|---|---|
| **آسان دیکھ بھال** | ایک ماڈیول میں مسئلہ ٹھیک کرنا کسی غیر متعلقہ ماڈیول کو خراب کرنے کا خطرہ نہیں رکھتا |
| **آسان جانچ (testing)** | ہر ماڈیول کو حقیقی کام سونپنے سے پہلے اکیلے چیک کیا جا سکتا ہے |
| **آسان اپ گریڈ** | AI Scoring فارمولے کو ڈیسک ٹاپ ایپ کو بالکل چھوئے بغیر دوبارہ کیلیبریٹ کیا جا سکتا ہے |
| **آسان ڈیبگنگ** | جب کچھ غلط ہو، تہہ دار ڈھانچہ یہ تنگ کر دیتا ہے کہ *کہاں* دیکھنا ہے |
| **دوبارہ استعمال ہونے والے ماڈیولز** | Decision Engine کو 5 سے زیادہ مختلف خصوصیات نے دوبارہ استعمال کیا ہے، ایک بار بھی دوبارہ لکھے بغیر |
| **مستقبل کی توسیع** | نئی خصوصیات (جیسے Portfolio Intelligence اور AI Performance) کو *اضافے* کے طور پر شامل کیا گیا، کبھی پہلے سے چلنے والی چیز کو دوبارہ لکھ کر نہیں |

یہ محض نظریہ نہیں — یہ بالکل وہی ہے جو ہوا۔ آپ کا پلیٹ فارم ایک اکیلے Market Scanner سے بڑھ کر کموڈٹیز، آن چین ٹوکنز، ایک AI گفتگو کرنے والا ایجنٹ، پورٹ فولیو تجزیات، اور کارکردگی کی نگرانی تک — کئی ڈیویلپمنٹ مراحل میں پھیلا، **کبھی بھی کسی پرانی خصوصیت کو پھینک کر دوبارہ بنانے کی ضرورت پیش آئے بغیر۔** یہی اس آرکیٹیکچر کا براہ راست، قابلِ پیمائش فائدہ ہے۔

### 💡 Owner Tip / مالک کے لیے مشورہ
🇬🇧 When evaluating whether a proposed change is "safe," ask: "does this fit inside one existing module's job, or does it need to reach into several at once?" The second kind needs more caution.
🇵🇰 جب یہ جانچنا ہو کہ کوئی تجویز کردہ تبدیلی "محفوظ" ہے یا نہیں، پوچھیں: "کیا یہ ایک موجودہ ماڈیول کے کام میں فٹ ہوتی ہے، یا اسے بیک وقت کئی میں جانا پڑتا ہے؟" دوسری قسم کو زیادہ احتیاط درکار ہے۔

### ⚠️ Common Mistake / عام غلطی
🇬🇧 Believing "modular" means "slower to build new features." In this project, the opposite has consistently been true — most new features were built faster *because* the foundation already existed to build on.
🇵🇰 یہ سمجھنا کہ "ماڈیولر" کا مطلب "نئی خصوصیات بنانے میں سست" ہے۔ اس پروجیکٹ میں مسلسل اس کے برعکس درست ثابت ہوا ہے — زیادہ تر نئی خصوصیات تیزی سے بنیں *کیونکہ* بنیاد پہلے سے موجود تھی جس پر تعمیر کی جا سکے۔

### 🧩 Real Example / حقیقی مثال
🇬🇧 The AI Performance Monitor was added by reusing the existing signal-history storage and the existing calibration system - a new screen and a new reporting module, not a new database or a new AI.
🇵🇰 AI Performance Monitor کو موجودہ سگنل ہسٹری اسٹوریج اور موجودہ کیلیبریشن سسٹم کو دوبارہ استعمال کر کے شامل کیا گیا - ایک نئی اسکرین اور ایک نیا رپورٹنگ ماڈیول، نہ کہ ایک نیا ڈیٹابیس یا نیا AI۔

### 🤔 Did You Know? / کیا آپ جانتے ہیں؟
🇬🇧 Every major addition to this platform went through a written, reviewed plan before any building started - architecture decisions here are never accidental.
🇵🇰 اس پلیٹ فارم میں ہر بڑا اضافہ تعمیر شروع ہونے سے پہلے ایک تحریری، جائزہ شدہ منصوبے سے گزرا - یہاں آرکیٹیکچر کے فیصلے کبھی حادثاتی نہیں ہوتے۔

### 📌 Chapter Summary / باب کا خلاصہ
🇬🇧 This architecture was chosen because it has already proven itself across many real feature phases - not because it looks good on paper.
🇵🇰 یہ آرکیٹیکچر اس لیے چُنا گیا کیونکہ یہ کئی حقیقی خصوصیات کے مراحل میں خود کو ثابت کر چکا ہے - نہ کہ صرف اس لیے کہ یہ کاغذ پر اچھا لگتا ہے۔

---

## 9. Owner Notes

### 🇬🇧 English — *This section is for you.*

**How to think about the architecture:**

Picture your software like a house. Some walls are **structural** — they hold the roof up, and moving them requires real care and understanding of what they support. Other walls are just **decoration** — you can repaint them, or knock one down to open up a room, with almost no risk. In your software:

- **Structural walls:** the AI Decision Engine's scoring logic, the calibration system, the "single source of truth" pattern described in Section 7.
- **Decoration:** which screen a feature appears on, what colors are used, how a table is laid out.

**Why modular software is valuable:**

It is the reason you can keep asking for new things — a new dashboard, a new report, a new question the Coach can answer — without the risk of the whole platform becoming fragile. Every phase this project has gone through proves this in practice.

**What should never be changed without understanding the consequences:**

- The AI Decision Engine's scoring formula — Evidence, Research, Coach, Portfolio Intelligence, and Performance Monitor all quietly depend on its output being trustworthy.
- The calibration system — it's what keeps crypto, gold, silver, and oil scored fairly against their own real histories instead of one blended, less accurate formula.
- The "layers only talk to their neighbor" rule from Section 2 — breaking it even once tends to make every future change riskier.

### 🇵🇰 اردو — *یہ حصہ صرف آپ کے لیے ہے۔*

**آرکیٹیکچر کے بارے میں کیسے سوچیں:**

اپنے سافٹ ویئر کو ایک گھر کی طرح تصور کریں۔ کچھ دیواریں **ساختی (structural)** ہیں — وہ چھت کو تھامے رکھتی ہیں، اور انہیں ہٹانے کے لیے واقعی احتیاط اور یہ سمجھنے کی ضرورت ہے کہ وہ کیا سہارا دیتی ہیں۔ دوسری دیواریں صرف **سجاوٹ** ہیں — آپ انہیں دوبارہ رنگ سکتے ہیں، یا کمرہ کھولنے کے لیے گرا سکتے ہیں، تقریباً بغیر کسی خطرے کے۔ آپ کے سافٹ ویئر میں:

- **ساختی دیواریں:** AI Decision Engine کی اسکورنگ منطق، کیلیبریشن سسٹم، سیکشن 7 میں بیان کیا گیا "سنگل سورس آف ٹروتھ" کا طریقہ۔
- **سجاوٹ:** کوئی خصوصیت کس اسکرین پر نظر آتی ہے، کون سے رنگ استعمال ہوتے ہیں، ٹیبل کیسے ترتیب دیا گیا ہے۔

**ماڈیولر سافٹ ویئر کیوں قیمتی ہے:**

یہی وہ وجہ ہے جس کی بدولت آپ نئی چیزیں مانگتے رہ سکتے ہیں — ایک نیا ڈیش بورڈ، ایک نئی رپورٹ، ایک نیا سوال جس کا Coach جواب دے — بغیر اس خطرے کے کہ پورا پلیٹ فارم کمزور ہو جائے۔ اس پروجیکٹ کا ہر مرحلہ عملی طور پر یہ ثابت کرتا ہے۔

**کیا کبھی بھی نتائج سمجھے بغیر نہیں بدلنا چاہیے:**

- AI Decision Engine کا اسکورنگ فارمولا — Evidence، Research، Coach، Portfolio Intelligence، اور Performance Monitor سب خاموشی سے اس کے نتیجے کے قابلِ بھروسہ ہونے پر منحصر ہیں۔
- کیلیبریشن سسٹم — یہی وہ چیز ہے جو کرپٹو، سونا، چاندی، اور تیل کو اُن کی اپنی حقیقی ہسٹری کے مطابق منصفانہ طریقے سے اسکور رکھتی ہے، بجائے ایک ملے جلے، کم درست فارمولے کے۔
- سیکشن 2 کا "تہیں صرف اپنی ساتھی تہہ سے بات کرتی ہیں" کا اصول — اسے ایک بار بھی توڑنا مستقبل کی ہر تبدیلی کو زیادہ خطرناک بنا دیتا ہے۔

---

## 10. Executive Summary

### 🇬🇧 English

Your software is organized into 8 clear layers — Desktop Application, API, Business Logic, AI Engine, Services, Repository Layer, Database, and External Providers — each with one job, each only talking to its direct neighbor. The AI itself is a disciplined 8-stage chain (Scanner → SMC → Indicators → AI Scoring → Decision Engine → Evidence → Research → Trading Coach) where explanation always follows decision, never leads it.

The single most important design choice in this whole system is **reuse over duplication**: nearly every advanced feature (Evidence, Research, Trading Coach, Portfolio Intelligence, Performance Monitor) reads from the same Decision Engine rather than building a competing one. This is why the platform could grow through many development phases — adding crypto, commodities, on-chain tokens, an AI agent, and portfolio tools — without ever needing to rebuild something that already worked.

Think of the architecture like a house: some parts are structural (the scoring logic, the calibration system, the layer-to-layer rule) and should never be touched without understanding what depends on them; most of the rest is decoration, safe to change freely. This distinction is the single most useful thing to remember from this document.

### 🇵🇰 اردو

آپ کا سافٹ ویئر 8 واضح تہوں میں منظم ہے — ڈیسک ٹاپ ایپلیکیشن، API، بزنس لاجک، AI انجن، سروسز، ریپوزیٹری لیئر، ڈیٹابیس، اور بیرونی فراہم کنندگان — ہر ایک کا ایک کام ہے، ہر ایک صرف اپنی براہ راست ساتھی تہہ سے بات کرتی ہے۔ AI خود بھی 8 مراحل کا ایک منظم سلسلہ ہے (Scanner ← SMC ← Indicators ← AI Scoring ← Decision Engine ← Evidence ← Research ← Trading Coach) جہاں وضاحت ہمیشہ فیصلے کے بعد آتی ہے، کبھی اس سے پہلے نہیں۔

اس پورے سسٹم کا سب سے اہم ڈیزائن فیصلہ ہے **دہرانے کی بجائے دوبارہ استعمال**: تقریباً ہر جدید خصوصیت (Evidence، Research، Trading Coach، Portfolio Intelligence، Performance Monitor) اُسی ایک Decision Engine سے پڑھتی ہے، ایک مقابل انجن بنانے کی بجائے۔ یہی وجہ ہے کہ پلیٹ فارم کئی ڈیویلپمنٹ مراحل سے گزر کر بڑھ سکا — کرپٹو، کموڈٹیز، آن چین ٹوکنز، AI ایجنٹ، اور پورٹ فولیو ٹولز شامل کرتے ہوئے — بغیر کبھی کسی پہلے سے کام کرنے والی چیز کو دوبارہ بنانے کی ضرورت کے۔

آرکیٹیکچر کو ایک گھر کی طرح سمجھیں: کچھ حصے ساختی ہیں (اسکورنگ منطق، کیلیبریشن سسٹم، تہہ سے تہہ کا اصول) اور انہیں کبھی بھی یہ سمجھے بغیر نہیں چھونا چاہیے کہ اُن پر کیا منحصر ہے؛ باقی زیادہ تر سجاوٹ ہے، آزادانہ طور پر بدلنے کے لیے محفوظ۔ یہی فرق اس دستاویز کی سب سے مفید بات ہے جو یاد رکھنی چاہیے۔

---

## What You Learned in Document 02 / اس دستاویز سے آپ نے کیا سیکھا

🇬🇧 You now understand: what software architecture means, the 8-layer structure of your entire platform, what each folder in the project is responsible for, how the AI itself is organized as a chain rather than one block, how data physically travels through a request, which modules depend on which and why duplication was avoided, why this design was chosen, and — most importantly — which parts of your software are "structural" and deserve real caution before changing.

🇵🇰 اب آپ سمجھ چکے ہیں: سافٹ ویئر آرکیٹیکچر کا کیا مطلب ہے، آپ کے پورے پلیٹ فارم کا 8 تہوں کا ڈھانچہ، پروجیکٹ کا ہر فولڈر کس کا ذمہ دار ہے، AI خود کو ایک بلاک کی بجائے ایک سلسلے کی صورت میں کیسے منظم کرتا ہے، ڈیٹا ایک درخواست کے دوران جسمانی طور پر کیسے سفر کرتا ہے، کون سے ماڈیولز کس پر منحصر ہیں اور دہرانے سے کیوں بچا گیا، یہ ڈیزائن کیوں چُنا گیا، اور — سب سے اہم — آپ کے سافٹ ویئر کے کون سے حصے "ساختی" ہیں اور بدلنے سے پہلے واقعی احتیاط کے مستحق ہیں۔

## Coming Next: Document 03 / آگے کیا آ رہا ہے: دستاویز 03

🇬🇧 **Document 03 — Data Flow & Request Lifecycle** will go one level deeper: it will walk through exactly what happens, step by step, from the moment you click a button in the desktop app to the moment a real answer appears on your screen — including what happens when something goes wrong along the way. Document 03 has not been written yet.

🇵🇰 **دستاویز 03 — ڈیٹا فلو اور درخواست کی زندگی کا سفر** ایک درجہ مزید گہرائی میں جائے گی: یہ بالکل قدم بہ قدم بتائے گی کہ جس لمحے آپ ڈیسک ٹاپ ایپ میں کوئی بٹن دباتے ہیں سے لے کر جس لمحے آپ کی اسکرین پر حقیقی جواب آتا ہے، کیا ہوتا ہے — بشمول یہ کہ اگر راستے میں کچھ غلط ہو جائے تو کیا ہوتا ہے۔ دستاویز 03 ابھی نہیں لکھی گئی۔
