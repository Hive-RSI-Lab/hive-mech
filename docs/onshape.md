# Handing an enclosure to Onshape

The person's original ask was an enclosure made in Onshape. mech models the
enclosure in code, so it can be built, checked and versioned in the Hive.
Onshape is where the person opens it, edits it and prints from it. The
STEP file moves it across.

## What the person gives

One of these two:

1. **Nothing.** They download `out/enclosure.step` (and the STLs) from the
   branch and use Insert → Import in their own Onshape document. Nothing in
   the Hive touches Onshape.
2. **An API key pair**, made at https://dev-portal.onshape.com/keys
   (access key and secret key, scoped to "read" and "write"). The key pair
   is a credential. It goes to the person through the lead, and a warden
   puts it in the environment of the one run that uses it. It is never
   written to a file, a commit, a message or a brief, and nothing asks for
   it in any other way.

## With the key

```
export ONSHAPE_ACCESS_KEY=... ONSHAPE_SECRET_KEY=...   # set by the warden for this run only
mech onshape check                                      # whose key it is
mech onshape upload out/enclosure.step --name "hifi Lite enclosure"
```

`upload` creates a new private document in the key owner's account, imports
the STEP into it as a part studio (`/api/v10/documents`, then
`/api/v10/translations`), waits for the import and prints the document's
URL. An enterprise account sets `ONSHAPE_URL`. `--dry-run` shows what it
would send and needs no key.

Uploading sends the design to the person's Onshape account. That is
outward-facing, so do it only when the brief or the person asks for it, and
put the document URL in your account.

## What doesn't cross

STEP carries solids, not the build123d script's parameters. An edit made in
Onshape stays in Onshape. To keep the code as the source, make the change
in the script and upload again as a new document or version. If the person
wants to work in Onshape from then on, say in the account that the code is
no longer the source.
