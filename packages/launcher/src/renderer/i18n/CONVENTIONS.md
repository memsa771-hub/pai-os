# Desktop copy conventions

The Placement AI desktop interface ships in English only.

- Put renderer copy in `locales/en/<namespace>.json` and use i18next keys from components.
- Keep product wording as “Placement AI”; do not introduce OpenAgents branding in customer-facing copy.
- Do not add a language picker or persist a UI-language preference.
- Keep strings concise and accessible. Product links belong in shared configuration, not scattered through components.
- Input must continue to support Unicode and IME composition even though the interface language is English.
