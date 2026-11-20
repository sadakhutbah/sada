# Changelog

## [0.1.0-alpha.9](https://github.com/sadakhutbah/sada/compare/sada-v0.1.0-alpha.8...sada-v0.1.0-alpha.9) (2026-11-20)


### Features

* **edge:** prepare the Cloudflare proxy, ingest host and chunked video upload ([36e505d](https://github.com/sadakhutbah/sada/commit/36e505d542fc66994361c785de3e9c5e80238809))

## [0.1.0-alpha.8](https://github.com/sadakhutbah/sada/compare/sada-v0.1.0-alpha.7...sada-v0.1.0-alpha.8) (2026-11-20)


### Features

* **monitoring:** scrape kubelet/cAdvisor for pod CPU and memory panels ([#60](https://github.com/sadakhutbah/sada/issues/60)) ([23a2bab](https://github.com/sadakhutbah/sada/commit/23a2bab3c9ac43fec4a0140e2407506479f53e88))
* **prayer-times:** own prayer-time sources, calculated and manual, independent of Mawaqit ([#62](https://github.com/sadakhutbah/sada/issues/62)) ([878ec14](https://github.com/sadakhutbah/sada/commit/878ec1451b0889f0d2cfe5ebf659e486cf1c22bc))

## [0.1.0-alpha.7](https://github.com/sadakhutbah/sada/compare/sada-v0.1.0-alpha.6...sada-v0.1.0-alpha.7) (2026-11-20)


### Features

* **telemetry:** export the post-live pipeline's LLM and transcription calls ([#58](https://github.com/sadakhutbah/sada/issues/58)) ([00f57e2](https://github.com/sadakhutbah/sada/commit/00f57e2db502bd06e2f47f0987d12dcd5d70782e))

## [0.1.0-alpha.6](https://github.com/sadakhutbah/sada/compare/sada-v0.1.0-alpha.5...sada-v0.1.0-alpha.6) (2026-11-20)


### Bug Fixes

* **monitoring:** allow the application's metric labels in the collector ([#56](https://github.com/sadakhutbah/sada/issues/56)) ([4a80301](https://github.com/sadakhutbah/sada/commit/4a80301bfc574226eb0e494fed6c81fd0e83bc77))
* **monitoring:** expose mosque label and align dashboard with real metrics ([#54](https://github.com/sadakhutbah/sada/issues/54)) ([b01fdc1](https://github.com/sadakhutbah/sada/commit/b01fdc1f6c4f6fd95bb3c7c35bffb8f02a76dbfe))
* **telemetry:** label back-office jobs and LLM calls with their mosque ([#57](https://github.com/sadakhutbah/sada/issues/57)) ([3db1cc6](https://github.com/sadakhutbah/sada/commit/3db1cc6d40eb0069f6ddc5f0f5f9526240620146))

## [0.1.0-alpha.5](https://github.com/sadakhutbah/sada/compare/sada-v0.1.0-alpha.4...sada-v0.1.0-alpha.5) (2026-11-19)


### Features

* **infra:** optional private monitoring VM (Tailscale only) ([#51](https://github.com/sadakhutbah/sada/issues/51)) ([613dc80](https://github.com/sadakhutbah/sada/commit/613dc80932f1e25f1f31665e70ad9d9047661dda))
* OpenTelemetry metrics and traces (opt-in) ([#52](https://github.com/sadakhutbah/sada/issues/52)) ([50e1000](https://github.com/sadakhutbah/sada/commit/50e10002f115c48b4e0ec571d5f58f2560a14c55))

## [0.1.0-alpha.4](https://github.com/sadakhutbah/sada/compare/sada-v0.1.0-alpha.3...sada-v0.1.0-alpha.4) (2026-11-19)


### Miscellaneous Chores

* release 0.1.0-alpha.4 ([93af734](https://github.com/sadakhutbah/sada/commit/93af7344b7b7478aede10d5dec5eba8039fc9937))

## [0.1.0-alpha.3](https://github.com/sadakhutbah/sada/compare/sada-v0.1.0-alpha.2...sada-v0.1.0-alpha.3) (2026-11-19)


### Miscellaneous Chores

* release 0.1.0-alpha.3 ([8a84f39](https://github.com/sadakhutbah/sada/commit/8a84f3915ef647c1208b66798341292b3aaf399c))

## [0.1.0-alpha.2](https://github.com/sadakhutbah/sada/compare/sada-v0.1.0-alpha.1...sada-v0.1.0-alpha.2) (2026-11-19)


### Features

* align transcript export on the dressed video (timing=video|raw) ([#35](https://github.com/sadakhutbah/sada/issues/35)) ([8698713](https://github.com/sadakhutbah/sada/commit/869871307ebb34029d40b3f39bcf4d43121a286e))
* export khutba transcript as .txt and .srt ([#33](https://github.com/sadakhutbah/sada/issues/33)) ([8d15d79](https://github.com/sadakhutbah/sada/commit/8d15d79bd8be1b67f7c96052bf9bd0e72493d8cb))

## [0.1.0-alpha.1](https://github.com/sadakhutbah/sada/compare/sada-v0.1.0-alpha.0...sada-v0.1.0-alpha.1) (2026-11-18)


### Features

* **backoffice:** download sermon subtitles as .srt per language ([3b60b55](https://github.com/sadakhutbah/sada/commit/3b60b5567e26a621cdc5762f5769615ddb4c666a))
* **backoffice:** serve sermon subtitles as inline WebVTT ([009bc62](https://github.com/sadakhutbah/sada/commit/009bc62e9821c7cf81f6d0d9ac9d91f3f20fc40d))
* **backoffice:** show the dressed video by default on the session page, raw kept as history ([d885251](https://github.com/sadakhutbah/sada/commit/d88525107a6913fa074ff3f862229d723632542a))
* **backoffice:** up-to-date line, branded video state, publication failure, menu failure badge ([#3](https://github.com/sadakhutbah/sada/issues/3)) ([a6ee35a](https://github.com/sadakhutbah/sada/commit/a6ee35a2ab0c47cc7f67b9dfea5cb3f177ba5625))
* Docker image and Compose for hosted multi-mosque (receiver + one orchestrator per mosque) ([0c5d943](https://github.com/sadakhutbah/sada/commit/0c5d943db77fb33484dbc4af93a336d6d4a4d8d0))
* **habillage:** keep the current dressed video until the new render replaces it ([bf7faf7](https://github.com/sadakhutbah/sada/commit/bf7faf7e72c20b8c43fafb2ff7a5c60110f0fd10))
* **honorifics:** formules de respect par langue et par mode ; plus de glossaire en prompt Groq ([e17b65d](https://github.com/sadakhutbah/sada/commit/e17b65d331eeeeef549bdd53c90778fcb05e280a))
* Mawaqit times cached per mosque in the database, served to orchestrators ([113ea3e](https://github.com/sadakhutbah/sada/commit/113ea3e9458ef0871685c6d31dd93915673dcaac))
* orchestrator sends a programme copy to the receiver for in-console viewing ([74dcf62](https://github.com/sadakhutbah/sada/commit/74dcf62d8fe9e6087efa61804bb25a12a3b2e095))
* ORM session (SQLAlchemy) as the back office connection, ORM classes on every table ([bbb1495](https://github.com/sadakhutbah/sada/commit/bbb14953c82243d79d50a26e8bdf397694d81b48))
* per-mosque orchestrator token; shared token only for single-mosque instances ([1f749fd](https://github.com/sadakhutbah/sada/commit/1f749fd46c73a2fbec5eb9e598e20b9a10c69bb7))
* per-mosque processing and model settings ([98104ed](https://github.com/sadakhutbah/sada/commit/98104ed1c13b4ccdbfbe35a8f3180934778e5a91))
* per-mosque stream key checked by the back office for the stream receiver ([6e793bc](https://github.com/sadakhutbah/sada/commit/6e793bc6efb64afc4d4bf8b8177e1c5e4705f7d6))
* **pipeline:** build per-language SRT subtitles from the transcript ([767026b](https://github.com/sadakhutbah/sada/commit/767026b3be204111010e9402e5766f295cfe777e))
* **pipeline:** optional distant transcription for mosques without a GPU ([5995a4c](https://github.com/sadakhutbah/sada/commit/5995a4c6e21faa9794f95ca94cb7c3286f3c6dd4))
* **pipeline:** shared language measurement and repair passes ([3c7abc6](https://github.com/sadakhutbah/sada/commit/3c7abc61b6cbdb7fd425d40fb75de48a366fbe3b))
* platform administrator for shared settings (model, transcription) ([6448371](https://github.com/sadakhutbah/sada/commit/6448371cfa9063a33a2c6e96a8b5e28de3d677a8))
* PostgreSQL support (SADA_DATABASE_URL), SQLite stays the default ([ce16d18](https://github.com/sadakhutbah/sada/commit/ce16d183ad515cdc6cfd7407ccdc5581688e0645))
* **processing:** pipeline reports its current step; khutba state shows step n of N and stalls per step ([#4](https://github.com/sadakhutbah/sada/issues/4)) ([b7b2a34](https://github.com/sadakhutbah/sada/commit/b7b2a340de6389fa99d6e2712940bc0d9cd8f5cc))
* programme stored per mosque in the database, served to orchestrators ([289d789](https://github.com/sadakhutbah/sada/commit/289d789f2dd56fa443c08265e8f9fb07ba1fbf16))
* **screens:** customizable live banner (khutba/dars) via the imported template ([0914000](https://github.com/sadakhutbah/sada/commit/0914000dd597e7abf6857363f39134c75fec9b1e))
* **search:** multilingual search over transcripts, verses and meaning ([#12](https://github.com/sadakhutbah/sada/issues/12)) ([0d3f2d7](https://github.com/sadakhutbah/sada/commit/0d3f2d7fd64098333f8c95dd813a031ed35fdcd0))
* stream key issued from the back office, shown once, re-showing issues a new one ([9fdfa6c](https://github.com/sadakhutbah/sada/commit/9fdfa6cc39292f2985565b7eef0093f5176dd1e7))
* **subtitles:** every track covers the whole sermon ([e7ace56](https://github.com/sadakhutbah/sada/commit/e7ace56f7eb3e8e514ee8e8747f401d7b19d9f17))
* **users:** accounts created or reset by an admin must pick their own password at first sign-in ([49e6f02](https://github.com/sadakhutbah/sada/commit/49e6f0258d12f4b295d39830519064ba56ded64e))
* **users:** create and manage accounts from the back-office, per-account permissions, admin password reset ([67c4a33](https://github.com/sadakhutbah/sada/commit/67c4a33a5d2d8a94afc04dd6bb825e84865a0c35))
* versioned alpha releases and version shown in the console ([6daa87a](https://github.com/sadakhutbah/sada/commit/6daa87a92eedc7dc9505a410b26067bd1790a3f8))
* **web:** branding screen no longer embeds the rendered video, download only ([70b9faf](https://github.com/sadakhutbah/sada/commit/70b9faf1dc80e9b7d696f9ad8534e3d4b83b44b9))
* **web:** grouped top bar with central search ([#13](https://github.com/sadakhutbah/sada/issues/13)) ([4d16104](https://github.com/sadakhutbah/sada/commit/4d1610479613a215cd6089a25aaedfad35243010))
* **web:** offer subtitle downloads on the habillage screen ([c14a467](https://github.com/sadakhutbah/sada/commit/c14a467d98352b5e5e23e6c61e0e664940de9404))
* **web:** show sermon subtitles in the player with a CC button ([949736f](https://github.com/sadakhutbah/sada/commit/949736fdcc9173cbcec55ac86dd4bd8b781ce2bb))
* **web:** tabs, tooltips and dialogs answer the keyboard and screen readers ([79ddead](https://github.com/sadakhutbah/sada/commit/79ddead6ec67da990481af61829c938bd99d3998))


### Bug Fixes

* accept postgresql+psycopg URLs, drop per-test schemas after the suite ([7e8e016](https://github.com/sadakhutbah/sada/commit/7e8e016e8ac02f46036551b757fc061b204d18c6))
* **backoffice:** find ffmpeg outside PATH for clip rendering ([a4af458](https://github.com/sadakhutbah/sada/commit/a4af4581c9dd687560c04b8697cfdb8f725fc2a4))
* **honorifics:** plus aucune formule ajoutée après « Allah » ([4e1d8a0](https://github.com/sadakhutbah/sada/commit/4e1d8a055f541ab7cec7783463df662efd2b7a39))
* **llm:** retry on 429 and chunk the second translation pass (Groq free tier) ([e393f0d](https://github.com/sadakhutbah/sada/commit/e393f0dfe50ad19dcc4a3f3e015bca61670fc5bd))
* monitoring copy via a separate ffmpeg, no dummy default destination ([eb6bcc2](https://github.com/sadakhutbah/sada/commit/eb6bcc276a777ef416831e02b2f8e20ba6208717))
* **pipeline:** distant transcription measures the language and reruns the repair passes ([9e86594](https://github.com/sadakhutbah/sada/commit/9e8659411e508f15f3927b9d3e267cec6db2d1e1))
* **pipeline:** never accept a retranscribed hallucination ([0efd8d6](https://github.com/sadakhutbah/sada/commit/0efd8d6befaa74f5d52b5e484414b3d94dc605b7))
* retry the back office at start and replan the day when it answers ([605283b](https://github.com/sadakhutbah/sada/commit/605283bf541dbf69c4f4b719ac5edeab10911447))
* **screens:** embed the mosque logo in template pages rendered by the engine ([121a995](https://github.com/sadakhutbah/sada/commit/121a9958d288c93b5ac26c245d42fe6b018fd917))
* **search:** search-index reports sermons already up to date instead of a bare 0 ([5853fb1](https://github.com/sadakhutbah/sada/commit/5853fb1b62859260ce07ebd84b03c597cff43e6a))
* **subtitles:** correct known Whisper misspellings (fitra) and apostrophe spacing ([8885c5c](https://github.com/sadakhutbah/sada/commit/8885c5c80af79945dd9dd270fec49640edc1c8ee))
* **subtitles:** translate phonetic Arabic in the French track, drop repetition loops ([a922975](https://github.com/sadakhutbah/sada/commit/a922975d2a9997fb8bb578b5b5dcaa116fdeb8b7))
* **translate:** no empty catch-up call; test the batched catch-up ([17092b6](https://github.com/sadakhutbah/sada/commit/17092b667d3a128f22d08d0bfe2303f05d81ee91))
* **web:** bundle the brand fonts so the header stays on one line on Windows ([92a8c15](https://github.com/sadakhutbah/sada/commit/92a8c15e9602a289b1d0f9b25109468dd9b4fbf4))
* **web:** call the stream key useState before the early return; deploy fixes ([d81854c](https://github.com/sadakhutbah/sada/commit/d81854c7792c2f8d279679629badd408ba93dc22))
* **web:** header can no longer wrap on large screens ([416483f](https://github.com/sadakhutbah/sada/commit/416483f351c42cfc7cd78c0ced1074e64cd18e7c))
* **web:** opaque square app icon so iOS no longer shows white corners ([758e937](https://github.com/sadakhutbah/sada/commit/758e9371db157b88c843bd32a00dced742efc0b3))
* **web:** proper volume icons in the player ([2f9b9ca](https://github.com/sadakhutbah/sada/commit/2f9b9cac1dd8642cca96df0f9381a88e0b09cb22))
* **web:** transcription settings name the engine actually in use ([669a8f9](https://github.com/sadakhutbah/sada/commit/669a8f9ce8f26f7253f39ecad55568e75fcd5dca))


### Performance Improvements

* **habillage:** rendu par segments (tête et queue réencodées, milieu copié) ([f42fbbe](https://github.com/sadakhutbah/sada/commit/f42fbbeb68566f8532b9eb8217094d9b7b0663f1))
* x264 superfast, veryfast left no margin on moving scenes ([bbda7ad](https://github.com/sadakhutbah/sada/commit/bbda7ad253162951dd3bcf35696a1413b3c6ce9e))
