/** Real Firestore Emulator assertions. No emulator means an explicit test skip. */
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import test from 'node:test';
import { assertFails, assertSucceeds, initializeTestEnvironment } from '@firebase/rules-unit-testing';
import { deleteApp, initializeApp } from 'firebase/app';
import {
  collection, connectFirestoreEmulator, deleteDoc, doc, getDocFromServer,
  getDocsFromServer, getFirestore, setDoc, setLogLevel, terminate, updateDoc,
} from 'firebase/firestore';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const projectId = 'demo-rag-harass-bot';
const databaseIds = ['(default)', 'app-dev', 'admin-private', 'admin-private-dev'];
const collections = [
  'runtime_config', 'scenario_scripts', 'rag_documents', 'rag_judgments', 'rag_remedies',
  'admin_credentials', 'admin_sessions', 'admin_rate_limits', 'admin_jobs',
  'corpus_sources', 'corpus_source_keys', 'corpus_content_keys', 'corpus_operations',
  'corpus_imports', 'corpus_source_assets',
];
const uid = 'rules-authenticated-user';
const emulatorAddress = process.env.FIRESTORE_EMULATOR_HOST;
setLogLevel('silent');

test('all four Firestore databases deny direct client access', {
  skip: !emulatorAddress && 'Requires Firestore Emulator; run pnpm test:rules',
  timeout: 180_000,
}, async t => {
  const address = new URL(`http://${emulatorAddress}`);
  assert(['127.0.0.1', 'localhost', '[::1]'].includes(address.hostname), 'Only a localhost emulator is allowed');
  assert(address.port, 'An explicit emulator port is required');
  assert(!process.env.GCLOUD_PROJECT || process.env.GCLOUD_PROJECT === projectId, 'Only the demo project is allowed');
  const host = address.hostname.replace(/^\[|\]$/g, '');
  const port = Number(address.port);
  const configuration = JSON.parse(await readFile(path.join(root, 'firebase.json'), 'utf8'));
  const preview = JSON.parse(await readFile(path.join(root, 'firebase.preview.json'), 'utf8'));
  assert.deepEqual(configuration.firestore.map(value => value.database).sort(), [...databaseIds].sort());
  assert.deepEqual(preview.firestore, configuration.firestore, 'Preview and production database rules must agree');
  const ruleFiles = await Promise.all(configuration.firestore.map(value => readFile(path.join(root, value.rules), 'utf8')));
  assert(ruleFiles.every(value => value === ruleFiles[0]), 'The pinned emulator requires the same rules for every database');
  const rules = ruleFiles[0];
  const environments = [];
  const namedApps = [];
  const namedDatabases = [];
  const loadRules = async content => {
    if (content) {
      // rules-unit-testing 5.x loads only the default DB. The pinned emulator
      // exposes its control endpoint per DB; never assume a project-level load
      // has changed the rules of an already-created named database.
      for (const databaseId of databaseIds) {
        const response = await fetch(`${address.origin}/emulator/v1/projects/${projectId}/databases/${databaseId}:securityRules`, {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ rules: { files: [{ name: 'firestore.rules', content }] } }),
        });
        assert(response.ok, `${databaseId}: emulator rejected rules (${response.status})`);
        const result = await response.json();
        assert(!result.issues?.some(issue => issue.severity === 'ERROR'), `${databaseId}: rules compilation failed`);
      }
    }
    const environment = await initializeTestEnvironment({ projectId, firestore: { host, port } });
    environments.push(environment);
    return environment;
  };
  const testEnvironment = await loadRules();
  const clients = new Map();
  for (const databaseId of databaseIds) {
    const identities = {};
    for (const identity of ['anonymous', 'authenticated']) {
      if (databaseId === '(default)') {
        const context = identity === 'authenticated'
          ? testEnvironment.authenticatedContext(uid, { email: 'synthetic@example.test', admin: true })
          : testEnvironment.unauthenticatedContext();
        identities[identity] = context.firestore();
      } else {
        // RulesTestContext.firestore() has no databaseId parameter. The public
        // modular SDK supports named DBs and the same emulator mock auth tokens.
        const app = initializeApp({ projectId }, `${databaseId}-${identity}`);
        namedApps.push(app);
        const database = getFirestore(app, databaseId);
        connectFirestoreEmulator(database, host, port, identity === 'authenticated'
          ? { mockUserToken: { sub: uid, user_id: uid, email: 'synthetic@example.test', admin: true } }
          : undefined);
        namedDatabases.push(database);
        identities[identity] = database;
      }
    }
    clients.set(databaseId, identities);
  }
  try {
    await t.test('startup configuration attaches denying rules to every named database', async () => {
      for (const databaseId of databaseIds) {
        const database = clients.get(databaseId).anonymous;
        await assertFails(getDocFromServer(doc(database, 'runtime_config', 'startup-rule-probe')));
      }
    });

    // A deny-only suite could pass with an accidentally anonymous auth context.
    // This temporary emulator-only rule proves request.auth.uid and the custom
    // claim are received for every database, while anonymous access is denied.
    await loadRules(`rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {
    match /rules_auth_probe/{document} {
      allow read, write: if request.auth != null
        && request.auth.uid == '${uid}' && request.auth.token.admin == true;
    }
  }
}`);
    let authIdentityVerified = false;
    await t.test('authenticated contexts carry Firebase Auth identity on all four databases', async () => {
      for (const databaseId of databaseIds) {
        const { anonymous, authenticated } = clients.get(databaseId);
        const reference = doc(authenticated, 'rules_auth_probe', 'identity');
        await assertSucceeds(setDoc(reference, { synthetic: true, databaseId, uid }));
        const snapshot = await assertSucceeds(getDocFromServer(reference));
        assert.equal(snapshot.data().uid, uid);
        await assertFails(getDocFromServer(doc(anonymous, 'rules_auth_probe', 'identity')));
        await assertFails(setDoc(doc(anonymous, 'rules_auth_probe', 'anonymous'), { synthetic: true }));
      }
      authIdentityVerified = true;
    });
    assert(authIdentityVerified, 'Do not report deny checks until the authenticated identity probe has passed');
    await loadRules(rules);

    for (const databaseId of databaseIds) {
      for (const identity of ['anonymous', 'authenticated']) {
        await t.test(`${databaseId}: ${identity} cannot get, list, create, update, or delete application data`, async () => {
          const database = clients.get(databaseId)[identity];
          for (const collectionId of collections) {
            const reference = doc(database, collectionId, 'synthetic-rule-test');
            await assertFails(getDocFromServer(reference));
            await assertFails(getDocsFromServer(collection(database, collectionId)));
            await assertFails(setDoc(reference, { synthetic: true }));
            await assertFails(updateDoc(reference, { synthetic: false }));
            await assertFails(deleteDoc(reference));
          }
          // The recursive deny must cover nested import embeddings as well.
          const nested = doc(database, 'corpus_imports', 'synthetic', 'embeddings', 'chunk-1');
          await assertFails(getDocFromServer(nested));
          await assertFails(setDoc(nested, { synthetic: true }));
          // Also deny an existing document created during the authentication probe.
          await assertFails(getDocFromServer(doc(database, 'rules_auth_probe', 'identity')));
          await assertFails(updateDoc(doc(database, 'rules_auth_probe', 'identity'), { changed: true }));
          await assertFails(deleteDoc(doc(database, 'rules_auth_probe', 'identity')));
        });
      }
    }
  } finally {
    // Restore committed deny rules even if an auth probe or assertion fails.
    await loadRules(rules);
    await Promise.all(namedDatabases.map(database => terminate(database)));
    await Promise.all(namedApps.map(app => deleteApp(app)));
    await Promise.all(environments.map(environment => environment.cleanup()));
  }
  t.diagnostic(JSON.stringify({ projectId, databases: databaseIds, collections: collections.length,
    denied_operations: databaseIds.length * 2 * (collections.length * 5 + 5),
    auth_identity_verified: true, live_cloud_requests: 0 }));
});
