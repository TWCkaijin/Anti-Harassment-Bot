# CI deployment policy review

The executable candidate is `scripts/provision_delivery.py`; exact role
definitions and resource allowlists are in `ci-deployment-policy.json`. This
revision has not been applied. It removes the previous project-wide Run Admin,
Functions Developer, Rules Admin, Firestore Index Admin, Build Editor, Firebase
Viewer, Artifact Reader, and Secret Viewer grants.

| Resource | Proposed access |
| --- | --- |
| Project metadata | Ten listed discovery/source-upload permissions in `ragDeployMetadata` |
| Existing `api`, `api_preview`, `admin_api`, `admin_api_preview` functions | `get`, `getIamPolicy`, `update`, bound directly to each function |
| Their four existing Cloud Run services | `get`, `getIamPolicy`, bound directly to each service |
| Six listed model/signing secrets | Secret Manager Viewer bound to each secret; no secret payload access |
| Four listed Client/Admin runtime service accounts | Service Account User bound to each account |
| Existing `asia-east1/gcf-artifacts` repository | `artifactregistry.repositories.get` only |
| Hosting | Unresolved: predefined project-level Hosting Admin; excluded by default |

`cloudfunctions.functions.sourceCodeSet` is required on the location parent for
the signed source-upload URL. It cannot be restricted to an existing function;
the separate function update grants determine which deployments can consume
that upload. See the [GenerateUploadUrl API](https://docs.cloud.google.com/functions/docs/reference/rest/v2/projects.locations.functions/generateUploadUrl).

Function bindings use direct resource IAM, as documented by [Cloud Run
functions access control](https://docs.cloud.google.com/functions/docs/securing/managing-access-iam).
No unsupported `resource.name` condition is used for Functions or Hosting.
Cloud Functions is absent from the supported resource-name list in the [IAM
condition attribute reference](https://docs.cloud.google.com/iam/docs/conditions-attribute-reference).

Firebase documents that [Hosting does not support custom
roles](https://firebase.google.com/docs/projects/iam/permissions#hosting).
Its site REST API also has no site-level IAM policy methods. Consequently,
project-wide `roles/firebasehosting.admin` cannot honestly be described as a
two-site or preview-channel-only grant. The script requires the separate
`--allow-project-hosting-admin` option to include it. That option must not be used
until the remaining broad grant has been reviewed and approved. Without it,
function deployment permissions can be provisioned, but Hosting CI is incomplete.

## Firebase CLI 15.33.0 findings

Inspected the installed 15.33.0 source under `lib/`:

- `requirePermissions.js`: missing project-level informational permissions are
  caught and logged; actual deployment API calls still enforce authorization.
- `deploy/functions/checkIam.js`: `checkServiceAccountIam` always tests the
  legacy `PROJECT@appspot.gserviceaccount.com` identity. A successful response
  reporting no `actAs` is a hard error, even when all four functions explicitly
  declare other runtime accounts. Do not grant legacy/default-account `actAs`
  merely to satisfy this inaccurate check.
- `gcp/iam.js`: `FIREBASE_SKIP_INFORMATIONAL_IAM=1` skips these CLI-only
  informational checks. If used by the deployment runner, actual Cloud APIs
  must still enforce the scoped grants above. No IAM policy or API enforcement
  is changed by this flag. This is a documented local-code compatibility choice,
  not evidence that live deployment has passed; the candidate has not been
  validated under the federated identity.
- `gcp/run.js`: `setInvokerUpdate` reads the current invoker binding and writes
  IAM only when its members differ. The candidate gives CI read permission,
  not write permission, so invoker-policy drift must be fixed by an operator.
- `gcp/proto.js`: including `public` in an invoker list reduces the entire list
  to `allUsers`. All four functions declare this policy. The worker helper no
  longer adds a redundant task-account Run Invoker binding; internal worker
  authorization still verifies signed OIDC audience and exact service-account
  identity in the application.
- `deploy/functions/ensure.js`: unchanged declared secret/account pairs do not
  trigger secret IAM changes. Initial secret grants remain operator-managed.
- `deploy/functions/release/index.js`: artifact cleanup policy checks are
  separate from function updates. Configure cleanup once as an operator;
  CI receives read-only repository metadata.

The first deployment of all four functions, API enablement, database/index/rules
changes, queues, Scheduler, secret grants, and artifact cleanup must be completed
using the operator workflow before restricted CI is enabled. This policy does
not permit CI to create or delete functions, modify IAM, submit arbitrary Cloud
Build jobs, read secret payloads directly, or change Firestore rules/indexes.
Deploying code as the four runtime identities still grants indirect access to
those identities' runtime capabilities; this is inherent to authorized code
deployment and remains limited to the named application identities.

Federation remains restricted to the numeric repository and owner IDs, `push`
events, and the exact `main/deploy-main.yml` or `dev/deploy-dev.yml` workflow
references. The shared deployment identity can update both environments; the
Hosting limitation also prevents channel-level IAM separation in this project.
Branch protections and workflow review therefore remain significant controls.
