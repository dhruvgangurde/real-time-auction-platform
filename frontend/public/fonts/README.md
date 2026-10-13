# Fonts

Self-hosted subsets of three SIL Open Font License 1.1 families (brief 3.1 and 3.4). Each family's `OFL.txt` sits beside its files, as the licence requires; a subset or instance is a modification, which the OFL allows (none of these families has a reserved font name).

| File | Source | What was done | Bytes |
|---|---|---|---|
| `anek-latin/AnekLatin.woff2` | Anek Latin `[wdth,wght]` (Ek Type, Google Fonts) | instanced to width 75 to 100 and weight 400 to 700, then subset | 67,768 |
| `spectral/Spectral-Regular.woff2` | Spectral Regular (Production Type, Google Fonts) | subset | 33,264 |
| `spectral/Spectral-Medium.woff2` | Spectral Medium | subset | 35,132 |
| `spectral/Spectral-SemiBold.woff2` | Spectral SemiBold | subset | 35,356 |
| `fragment-mono/FragmentMono-Regular.woff2` | Fragment Mono Regular | subset | 27,628 |

Preloaded on first paint (brief 11.5 budget 120 KB): Spectral Regular and Anek Latin, 101,032 bytes together.

## Recipe

fontTools 4.63 with brotli (run under `uv run --with fonttools==4.63.0 --with brotli`):

1. Anek Latin: `fontTools.varLib.instancer.instantiateVariableFont(font, {"wdth": (75, 100), "wght": (400, 700)})`. The source's default weight is 500 and stays the default inside the kept range.
2. Every file: `fontTools.subset` with `--unicodes=U+0020-007E,U+00A0-00FF,U+2010-2027,U+2030-205E,U+20B9,U+2116,U+2212`, `--layout-features=*` (keeps `tnum`, `lnum`, small caps), `--flavor=woff2`.
3. Checks on every output: ₹ (U+20B9) and № (U+2116) in the cmap; the `tnum` feature present in Spectral and Anek (Fragment Mono is monospaced); the Anek axes are exactly wdth 75 to 100 and wght 400 to 700.

## Fallback metrics (`src/styles/fonts.css`)

So the swap from the local fallback to the web face does not move the layout, each fallback `@font-face` carries `size-adjust` and metric overrides measured from the real files: `size-adjust = avg(web) / avg(fallback)` over the advance widths of a sample of lower-case text, spaces and digits; `ascent-override`, `descent-override` and `line-gap-override` are the web face's hhea metrics (OS/2 typo metrics when `USE_TYPO_METRICS` is set) as a fraction of the em, divided by the size adjustment. Georgia and Segoe UI were measured from the Windows installs; Roboto from Google Fonts' variable file at width 100, weight 400, downloaded only to measure and not shipped.

| Web face | Fallback | size-adjust | ascent | descent | line-gap |
|---|---|---|---|---|---|
| Spectral Regular | Georgia | 97.15% | 109.01% | 47.66% | 0% |
| Anek Latin 400 | Segoe UI | 93.93% | 95.82% | 21.29% | 0% |
| Anek Latin 400 | Roboto | 93.65% | 96.10% | 21.36% | 0% |
