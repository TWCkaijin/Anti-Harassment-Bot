# Firestore Rules Emulator tests

These tests use the real Java Firestore emulator and the Firebase client SDK. They never connect to the live project. All calls require a loopback `FIRESTORE_EMULATOR_HOST` and use the fixed project ID `demo-rag-harass-bot`.

With Node.js 24, pnpm 10.27.0, Java 21 or later, and Firebase CLI 15.33.0 installed, run from the repository root:

```bash
pnpm install --frozen-lockfile
pnpm test:rules
```

The regular `pnpm -r test` command explicitly reports this suite as skipped when no emulator is running. The dedicated `firestore-rules` CI job launches the emulator and must pass before deployment.

The suite checks `(default)`, `app-dev`, `admin-private`, and `admin-private-dev`. For both anonymous clients and Firebase Auth identities carrying an `admin: true` claim, it asserts permission-denied responses for document reads, collection queries, creates, updates, and deletes across configuration, Skills, RAG, credential, session, job, and corpus collections. Nested import embeddings and existing documents are covered too.

A temporary emulator-only rule first permits access only when `request.auth.uid` and the custom claim match. Successful authenticated read/write and rejected anonymous requests prove that every authenticated context actually carries its identity. The committed deny rules are then restored for the denial matrix and again in `finally`. The test fails if this identity probe fails; it cannot count anonymous denials as authenticated coverage.

Firebase CLI 15.33.0 ignores rule files when its Firestore configuration contains multiple entries. Therefore `firebase.emulator.json` supplies the common committed rule file once; the suite verifies startup denial on each database. During the auth probe it applies rules through the emulator's per-database `:securityRules` endpoint, because `rules-unit-testing` 5.x loads only the default database. It requires all four deployed database configurations to reference identical rule content; changing that invariant fails the suite until the harness is updated. The endpoint behavior is verified against the CLI's pinned Firestore emulator 1.22.0.

This proves emulator rule behavior, not deployed rules or Admin SDK IAM isolation. References: [Firebase rule testing](https://firebase.google.com/docs/rules/unit-tests), [named emulator databases](https://firebase.google.com/docs/emulator-suite/connect_firestore), and [mock authentication for the emulator](https://firebase.google.com/docs/reference/js/firestore#connectfirestoreemulator).
