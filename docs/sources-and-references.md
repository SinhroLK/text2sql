# Registar izvora i literature

**Projekat:** Text-to-SQL master rad  
**Verzija registra:** 1.0  
**Poslednje ažuriranje:** 2026-08-13  
**Vlasnik registra:** autor projekta

Ovaj dokument je jedinstveno mesto za evidenciju svakog naučnog rada, dataseta,
repozitorijuma, tehničke dokumentacije i internog materijala koji utiče na dizajn,
implementaciju, evaluaciju ili tekst master rada.

Upis u registar ne znači automatski da izvor mora biti citiran u konačnoj verziji
master rada. Status i kolona „Upotreba” pokazuju da li je izvor već korišćen,
predstavlja samo pozadinsku literaturu ili je planiran za kasniju fazu.

## Pravila održavanja

1. Novi izvor se unosi pre nego što se njegova ideja, kod, podatak ili rezultat
   primeni u projektu.
2. Svaki zapis dobija stabilan ID koji se kasnije ne menja.
3. Za naučne radove prednost imaju DOI, izdavač i arXiv stranica, a ne sekundarni
   blogovi ili prepričavanja.
4. Za dataset ili repozitorijum obavezno se beleže commit/tag, datum preuzimanja,
   licenca i checksum svih ulaznih fajlova koji utiču na rezultat.
5. Za tehničku dokumentaciju beleže se korišćena verzija biblioteke i datum
   pristupa, jer se dokumentacija može promeniti.
6. Ako se izvor zameni, stari zapis se ne briše. Dobija status `SUPERSEDED` i
   pokazivač na novi izvor.
7. Svaka eksperimentalna odluka treba da navede ID izvora u konfiguraciji,
   ADR-u, eksperimentalnom protokolu ili izveštaju.
8. `.env`, API ključevi, lozinke, connection stringovi i sadržaj produkcionih
   baza nikada se ne upisuju u ovaj registar.
9. Bibliografske podatke treba ponovo proveriti pre predaje rada i izvesti u
   formatu koji zahteva fakultet.

## Statusi

| Status | Značenje |
|---|---|
| `ACTIVE` | Izvor trenutno direktno utiče na projekat ili metodologiju |
| `BACKGROUND` | Korišćen za teorijsku osnovu ili pregled literature |
| `PLANNED` | Izabran, ali komponenta koja ga koristi još nije implementirana |
| `LEGACY` | Korišćen u ranijem prototipu; nije izvor produkcionog koda ili finalnih rezultata |
| `SUPERSEDED` | Zamenjen novijim izvorom, verzijom ili odlukom |
| `REJECTED` | Razmotren, ali namerno nije korišćen; razlog ostaje zabeležen |

## 1. Izvori koji određuju aktuelnu arhitekturu

| ID | Status | Izvor | Upotreba u projektu |
|---|---|---|---|
| `PAPER-SPIDER2-001` | `ACTIVE` | F. Lei et al., *Spider 2.0: Evaluating Language Models on Real-World Enterprise Text-to-SQL Workflows*, arXiv:2411.07763, 2024. <https://arxiv.org/abs/2411.07763> | Osnova za izbor modernog evaluacionog benchmarka i opis realnih Text-to-SQL izazova. |
| `PAPER-XIYAN-001` | `PLANNED` | Y. Gao et al., *A Preview of XiYan-SQL: A Multi-Generator Ensemble Framework for Text-to-SQL*, arXiv:2411.08599, v3, 2025. <https://arxiv.org/abs/2411.08599> | Primarni izvor za M-Schema reprezentaciju; ideje o više kandidata i refineru koriste se samo u pojednostavljenoj opcionoj konfiguraciji. |
| `PAPER-SCHEMA-001` | `PLANNED` | M. Glass et al., *Extractive Schema Linking for Text-to-SQL*, arXiv:2501.17174, 2025. <https://arxiv.org/abs/2501.17174> | Osnova za extractive schema linking i kontrolisanje odnosa precision/recall pri izboru relevantne šeme. |
| `PAPER-DSPY-001` | `PLANNED` | O. Khattab et al., *DSPy: Compiling Declarative Language Model Calls into Self-Improving Pipelines*, arXiv:2310.03714, 2023. <https://arxiv.org/abs/2310.03714> | Osnova za automatizovanu optimizaciju prompt programa umesto ručnog biranja prompta. |
| `PAPER-P2SQL-001` | `PLANNED` | R. Pedro et al., *From Prompt Injections to SQL Injection Attacks: How Protected is Your LLM-Integrated Web Application?*, arXiv:2308.01990, v4, 2025. <https://arxiv.org/abs/2308.01990> | Osnova za prompt-to-SQL threat model, adversarial testove i input guardrails. |
| `PAPER-CHASE-001` | `BACKGROUND` | M. Pourreza et al., *CHASE-SQL: Multi-Path Reasoning and Preference Optimized Candidate Selection in Text-to-SQL*, arXiv:2410.01943, 2024. <https://arxiv.org/abs/2410.01943> | Inspiracija za opciono generisanje više kandidata i izbor kandidata; puna multi-agent reprodukcija je van MVP obima. |
| `PAPER-MACSQL-001` | `BACKGROUND` | B. Wang et al., *MAC-SQL: A Multi-Agent Collaborative Framework for Text-to-SQL*, arXiv:2312.11242, v6, 2025. <https://arxiv.org/abs/2312.11242> | Inspiracija za ograničeni refiner i rad sa greškama; puna multi-agent arhitektura je van MVP obima. |

## 2. Dataseti, benchmark izvori i evaluatori

| ID | Status | Izvor i zaključana verzija | Upotreba i reproduktivnost |
|---|---|---|---|
| `DATA-SPIDER2-REPO-001` | `ACTIVE` | Zvanični `xlang-ai/Spider2` repozitorijum: <https://github.com/xlang-ai/Spider2>, commit `cafb867313aab4e674652054198f383cf4018943` | Jedini dozvoljeni Spider2 snapshot za aktuelni protokol. Mutable `main` se ne koristi direktno. |
| `DATA-SPIDER2-SITE-001` | `ACTIVE` | Zvanični Spider 2.0 sajt: <https://spider2-sql.github.io/> | Opis benchmarka, režima evaluacije i obavezne oznake `oracle tables`. |
| `DATA-SPIDER2-LITE-001` | `ACTIVE` | `spider2-lite/spider2-lite.jsonl`, SHA-256 `4ba48916576fbd60311a2478c6d4550b5d8cf3fcbc512457ea493b5941ca009d` | Izvor 135 SQLite instanci; koristi se sopstvena DB-disjoint podela 31 development / 104 test. |
| `DATA-SPIDER2-EVAL-001` | `ACTIVE` | Zvanični evaluator i prateći fajlovi iz zaključanog Spider2 commita; checksumovi su u `configs/datasets/spider2-lite-sqlite-v1.toml` | Osnova za budući evaluator wrapper i execution-result metriku. |
| `DATA-SPIDER2-SPLIT-001` | `ACTIVE` | `configs/datasets/spider2-lite-sqlite-split-v1.json` | Kanonski spisak development/test baza i instance ID-jeva. Rezultat se naziva `Spider2-Lite SQLite custom DB-disjoint test split`, a ne puni Spider2-Lite score. |
| `DATA-SPIDER1-001` | `PLANNED` | Zvanični Spider 1.0 train split; tačan upstream commit, licenca i checksum biće dodati tokom `DATA-002`/`RET-001` | Planirani eksterni retrieval/few-shot korpus. Spider2 primeri su zabranjeni u retrieval indeksu. |
| `DATA-LEGACY-CSV-001` | `LEGACY` | Originalni `spider_text_sql.csv`; hash je naveden u odeljku 5 | Raniji dvokolonski eksperimentalni skup. Ne koristi se za finalnu evaluaciju jer nema `db_id`, šeme i pouzdan zvanični split. |

## 3. Naučna literatura iz postojeće prijave master rada

Ovi izvori su nasleđeni iz prijave teme. Pre konačnog citiranja treba proveriti
metapodatke, relevantnost tvrdnji i da li postoji novija izdavačka verzija.
Duplikat rada Peng et al. iz originalne bibliografije ovde je spojen u jedan zapis.

| ID | Status | Referenca | Tema/upotreba |
|---|---|---|---|
| `LIT-SURVEY-001` | `BACKGROUND` | X. Liu et al., *A Survey of Text-to-SQL in the Era of LLMs: Where are we, and where are we going?*, arXiv:2408.05109, 2024. <https://arxiv.org/abs/2408.05109> | Životni ciklus NL2SQL sistema, podaci, evaluacija i analiza grešaka. |
| `LIT-SURVEY-002` | `BACKGROUND` | L. Shi et al., *A Survey on Employing Large Language Models for Text-to-SQL Tasks*, ACM Computing Surveys, 2025. <https://doi.org/10.1145/3737873> | Prompt engineering, fine-tuning, benchmarkovi, primene i izazovi. Zamenjuje arXiv v1 zapis 2407.15186 kao glavni citat. |
| `LIT-SURVEY-003` | `BACKGROUND` | Y. Huang et al., *Exploring the Landscape of Text-to-SQL with Large Language Models: Progresses, Challenges and Opportunities*, arXiv:2505.23838, 2025. <https://arxiv.org/abs/2505.23838> | Pregled pre-processing, in-context learning, fine-tuning i post-processing faza. |
| `LIT-SURVEY-004` | `BACKGROUND` | A. Singh et al., *A Survey of Large Language Model-Based Generative AI for Text-to-SQL: Benchmarks, Applications, Use Cases, and Challenges*, arXiv:2412.05208, 2024. <https://arxiv.org/abs/2412.05208> | Pregled benchmarkova i praktičnih primena. |
| `LIT-SEC-001` | `BACKGROUND` | X. Peng et al., *On the Security Vulnerabilities of Text-to-SQL Models*, arXiv:2211.15363, 2022. <https://arxiv.org/abs/2211.15363> | Bezbednosne ranjivosti, napadi i data poisoning. |
| `LIT-SYNTAX-001` | `BACKGROUND` | T. Yu et al., *SyntaxSQLNet: Syntax Tree Networks for Complex and Cross-Domain Text-to-SQL Task*, arXiv:1810.05237, 2018. <https://arxiv.org/abs/1810.05237> | Istorijski razvoj sintaksno vođenog dekodiranja. |
| `LIT-GRAMMAR-001` | `BACKGROUND` | K. Lin et al., *Grammar-based Neural Text-to-SQL Generation*, arXiv:1905.13326, 2019. <https://arxiv.org/abs/1905.13326> | Grammar-based neural pristupi. |
| `LIT-RASAT-001` | `BACKGROUND` | J. Qi et al., *RASAT: Integrating Relational Structures into Pretrained Seq2Seq Model for Text-to-SQL*, arXiv:2205.06983, 2022. <https://arxiv.org/abs/2205.06983> | Relacije šeme u seq2seq modelima. |
| `LIT-ZEROSHOT-001` | `BACKGROUND` | Z. Gu et al., *Interleaving Pre-trained Language Models and Large Language Models for Zero-Shot NL2SQL Generation*, arXiv:2306.08891, 2023. <https://arxiv.org/abs/2306.08891> | Zero-shot NL2SQL i kombinovanje modela. |
| `LIT-RETRIEVAL-001` | `BACKGROUND` | C. Guo et al., *Prompting GPT-3.5 for Text-to-SQL with De-semanticization and Skeleton Retrieval*, arXiv:2304.13301, 2023. <https://arxiv.org/abs/2304.13301> | Skeleton/similarity retrieval za few-shot demonstracije. |
| `LIT-SQLPALM-001` | `BACKGROUND` | R. Sun et al., *SQL-PaLM: Improved Large Language Model Adaptation for Text-to-SQL*, arXiv:2306.00739, 2023. <https://arxiv.org/abs/2306.00739> | LLM adaptacija za Text-to-SQL. |
| `LIT-DYNAMIC-001` | `BACKGROUND` | C. Guo et al., *Retrieval-Augmented GPT-3.5-Based Text-to-SQL Framework with Sample-Aware Prompting and Dynamic Revision Chain*, arXiv:2307.05074, 2023. <https://arxiv.org/abs/2307.05074> | Retrieval, sample-aware prompting i revizija kandidata. |
| `LIT-CODES-001` | `BACKGROUND` | H. Li et al., *CODES: Towards Building Open-Source Language Models for Text-to-SQL*, arXiv:2402.16347, 2024. <https://arxiv.org/abs/2402.16347> | Open-source Text-to-SQL modeli i podaci. |
| `LIT-ULMFIT-001` | `BACKGROUND` | J. Howard and S. Ruder, *Universal Language Model Fine-tuning for Text Classification*, arXiv:1801.06146, 2018. <https://arxiv.org/abs/1801.06146> | Istorijski kontekst transfer learninga; nije GPT-1 rad i tu tvrdnju iz stare prijave treba ispraviti. |
| `LIT-GPT3-001` | `BACKGROUND` | T. Brown et al., *Language Models are Few-Shot Learners*, arXiv:2005.14165, 2020. <https://arxiv.org/abs/2005.14165> | Osnova za zero-shot/few-shot terminologiju i GPT-3. |
| `LIT-SQLQUALITY-001` | `BACKGROUND` | S. Sarker et al., *Enhancing LLM Fine-Tuning for Text-to-SQLs by SQL Quality Measurement*, arXiv:2410.01869, 2024. <https://arxiv.org/abs/2410.01869> | Merenje kvaliteta SQL-a pri fine-tuning pristupu; fine-tuning nije deo MVP-a. |
| `LIT-STARSQL-001` | `BACKGROUND` | M. He et al., *STAR-SQL: Self-Taught Reasoner for Text-to-SQL*, arXiv:2502.13550, 2025. <https://arxiv.org/abs/2502.13550> | Savremeni reasoning pristup i buduće poređenje. |
| `LIT-AI-BOOK-001` | `BACKGROUND` | P. Janičić i M. Nikolić, *Veštačka inteligencija*, Matematički fakultet, Beograd, 2023. | Teorijske osnove veštačke inteligencije. |

## 4. Sekundarni i tehnički izvori iz postojeće prijave

Sekundarni izvori mogu pomoći za orijentaciju, ali ključne akademske tvrdnje u
master radu treba, kad god je moguće, potkrepiti recenziranim ili primarnim
izvorima.

| ID | Status | Izvor | Napomena |
|---|---|---|---|
| `WEB-SEMKERNEL-001` | `BACKGROUND` | C. Rickman, *Use natural language to execute SQL queries*, Microsoft Semantic Kernel Blog, 2023. <https://devblogs.microsoft.com/semantic-kernel/use-natural-language-to-execute-sql-queries/> | Praktičan primer NL interfejsa prema SQL-u; nije primarni dokaz za performanse. |
| `WEB-K2VIEW-001` | `BACKGROUND` | I. Zarecki, *LLM Text-to-SQL Solutions: Top Challenges and Tips*, K2View, 2025. <https://www.k2view.com/blog/llm-text-to-sql/> | Sekundarni pregled praktičnih izazova. |
| `WEB-MEDIUM-001` | `BACKGROUND` | V. Q. Ha, *Bridging Natural Language and Databases: Best Practices for LLM-Generated SQL*, Medium, 2025. | Sekundarni izvor; zameniti primarnim izvorom za ključne tvrdnje o bezbednosti. |
| `WEB-GFG-001` | `BACKGROUND` | GeeksforGeeks, *What is a Large Language Model (LLM)*, 2025. | Samo pomoćni edukativni izvor; za teorijske tvrdnje koristiti knjigu ili primarni rad. |

## 5. Početni projektni materijali

Ovo su ulazi iz prototipa i prijave master rada. Hash predstavlja verziju koja je
analizirana pri formiranju aktuelne arhitekture. Kopije notebookova i skripti u
`notebooks/legacy/` ostaju istorijski materijal, ne produkcioni izvor koda.

| ID | Status | Materijal | SHA-256 | Upotreba |
|---|---|---|---|---|
| `INTERNAL-REC-001` | `ACTIVE` | `Preporuka-unapredjenja.txt` | `36e65898be824d88ed51ab241d806ff4dcd6a48f825c0d039e029ece516c4ed1` | Polazna preporuka za Spider2, M-Schema, retrieval, DSPy, guardrails, linking i refiner. |
| `INTERNAL-THESIS-001` | `ACTIVE` | `Predmet_Zavrsnog_Rada_Luka_Krstic_2024_3165.pdf` | `98ad5a901a37f85a87c004616155ce7ae54de6aa09212233c8ec0f22e961a9ee` | Prijava teme, postojeći pregled literature, ciljevi i okvir master rada. |
| `INTERNAL-NB-LLAMA33-001` | `LEGACY` | `llama3.3_70b.ipynb` | `313de1516d57ded02b08368a36c97cb1833fde2a2e173606af32a4ef8e617b99` | Raniji Groq/Llama 3.3 eksperiment. |
| `INTERNAL-NB-LLAMA4-001` | `LEGACY` | `llama4_scout_test.ipynb` | `e84ffe01a149323f3e14e1b643a67d9c485aff04577e6f510c8acf2abfa3a739` | Raniji Groq/Llama 4 Scout eksperiment. |
| `INTERNAL-APP-001` | `LEGACY` | `app.py` | `4ae46a44b794e4feba1f072ef39b76e42ae187ae83a95a0b918d84743f735e40` | Raniji Gradio/MySQL prototip; ne izvršava se kao finalni pipeline. |
| `INTERNAL-GENERATOR-001` | `LEGACY` | `csv_generator.py` | `08e8679a68a63dd2911eabc7e45c44bd015f245e8f3cc5af2335c04dae1dc17a` | Raniji generator eksperimentalnog CSV-a. |
| `INTERNAL-CSV-001` | `LEGACY` | `spider_text_sql.csv` | `4871266860ff6f1a5b5608d890b137cbbe49950b03edd5aca9b410b1bab21e31` | Stari dataset; zabranjen za finalnu evaluaciju. |
| `INTERNAL-README-001` | `LEGACY` | Originalni `README.md` | `ad76c8e88d10cd7983d9f49e13de81bc776c0b80675596295e1272e11b2f1d75` | Dokumentacija prethodnog prototipa. |
| `INTERNAL-REQ-001` | `LEGACY` | Originalni `requirements.txt` | `75315830ca078820f7c8ef4ee98543c1e653758566e47f68ff0fc4b4373de2a3` | Evidencija zavisnosti prethodnog prototipa. |
| `INTERNAL-GITIGNORE-001` | `LEGACY` | Originalni `.gitignore` | `5bde4c0778400138497dd87d36f6bd3d7c410c75b471b5cf78d2660559da27f8` | Ulaz za proveru ranijih pravila ignorisanja tajni i artefakata. |

Sadržaj originalnog `.env` fajla nije analiziran niti evidentiran. Aktivni ključevi,
lozinke i kredencijali nisu dozvoljeni ni u ovom registru ni u repozitorijumu.

## 6. Tehnička dokumentacija

Ovaj odeljak se ažurira kada odgovarajuća komponenta zaista uđe u implementaciju.
Za svaku biblioteku tada treba dodati zaključanu verziju iz `requirements.lock`.

| ID | Status | Dokumentacija | Planirana upotreba |
|---|---|---|---|
| `DOC-PYTHON-001` | `ACTIVE` | Python 3.11 dokumentacija: <https://docs.python.org/3.11/> | Standardna biblioteka, `sqlite3`, `dataclasses`, `tomllib`, CLI i testovi. |
| `DOC-SQLITE-001` | `ACTIVE` | SQLite dokumentacija: <https://www.sqlite.org/docs.html> | Introspekcija fixture baze i kasniji read-only sandbox. |
| `DOC-DSPY-001` | `PLANNED` | Zvanična DSPy dokumentacija/repozitorijum: <https://github.com/stanfordnlp/dspy> | Implementacija i zaključavanje B5 programa u Fazi 4. |
| `DOC-GROQ-001` | `PLANNED` | Zvanična Groq API dokumentacija: <https://console.groq.com/docs> | Realni LLM adapter; tačan model ID i datum pristupa ulaze u run manifest. |
| `DOC-GRADIO-001` | `PLANNED` | Zvanična Gradio dokumentacija: <https://www.gradio.app/docs> | Finalni demo koji koristi isti pipeline kao eksperimenti. |
| `DOC-SQLGLOT-001` | `PLANNED` | Zvanična SQLGlot dokumentacija/repozitorijum: <https://github.com/tobymao/sqlglot> | Kandidat za AST parsiranje i validaciju; konačna odluka se evidentira ADR-om. |

## 7. Veza izvora sa projektnim odlukama

| Odluka/komponenta | Obavezni izvori |
|---|---|
| Spider2-Lite SQLite protokol (`DATA-001`) | `PAPER-SPIDER2-001`, `DATA-SPIDER2-REPO-001`, `DATA-SPIDER2-SITE-001`, `DATA-SPIDER2-LITE-001`, `DATA-SPIDER2-EVAL-001` |
| M-Schema (`SCHEMA-002`) | `PAPER-XIYAN-001` |
| Extractive schema linking (`LINK-001`, `LINK-002`) | `PAPER-SCHEMA-001` |
| Similarity few-shot retrieval (`RET-001`, `RET-002`) | `LIT-RETRIEVAL-001`, `LIT-DYNAMIC-001`, `DATA-SPIDER1-001` |
| DSPy optimizacija (`DSPY-001`) | `PAPER-DSPY-001`, `DOC-DSPY-001` |
| Više kandidata i refiner (`REFINE-001`) | `PAPER-XIYAN-001`, `PAPER-CHASE-001`, `PAPER-MACSQL-001` |
| Prompt-to-SQL bezbednosna evaluacija (`SEC-002`) | `PAPER-P2SQL-001`, `LIT-SEC-001` |
| AST validator i SQLite sandbox (`SAFE-001`, `SAFE-002`) | `DOC-SQLITE-001`; izabrani AST parser se dodaje nakon ADR odluke |

## 8. Šablon za novi zapis

Kopirati ovaj blok pri dodavanju izvora koji ne može uredno da stane u tabelu:

```text
ID:
Status: ACTIVE | BACKGROUND | PLANNED | LEGACY | SUPERSEDED | REJECTED
Tip: paper | dataset | repository | documentation | internal
Puna referenca/naslov:
Autori/organizacija:
Godina i verzija:
DOI/arXiv/URL:
Datum pristupa:
Licenca:
Commit/tag/checksum (ako postoji):
Zašto je izvor korišćen:
Gde je primenjen u projektu:
Ograničenja/napomene:
Zamenjuje ili je zamenjen izvorom:
```

## 9. Dnevnik izmena registra

| Datum | Verzija | Izmena |
|---|---|---|
| 2026-08-13 | 1.0 | Kreiran centralni registar; evidentirani aktuelni arhitektonski izvori, zaključani Spider2 protokol, literatura iz prijave, početni projektni materijali i planirana tehnička dokumentacija. |
