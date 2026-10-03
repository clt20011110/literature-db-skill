# PDF download mode

Download only papers already represented by metadata identities, unless the user explicitly combines initialization/update with downloading.

## Prepare the queue

1. Query eligible records and existing `work_location` rows or download sidecars. Skip a paper when a validated local file with the same canonical identity/checksum already exists.
2. Prefer records with a visible official `pdf_url`. Otherwise queue the canonical landing page for Browser discovery.
3. Default destination when the user does not provide one: `<db-home>/downloads/<venue>/<year>/`. Use a stable filename such as `<native-id-or-doi-safe>__<short-title>.pdf` and retain the original title in metadata.
4. Save progress after every paper or small batch so the queue is resumable.

## Path A: a PDF link already exists

- For a stable public official URL, download directly with normal rate limiting. If access depends on the user's authorized publisher session, use the in-app Browser rather than copying cookies or signed URLs into code.
- Do not reuse an expired signed/session URL from old metadata. Re-open the landing page to obtain a fresh visible action.
- Validate the result before marking success: HTTP/content type when available, `%PDF-` magic bytes, non-HTML content, parseable page count, nonzero sensible size, and SHA-256 checksum.

## Path B: no PDF link exists

1. Open the canonical landing page in the in-app Browser and apply the slow-page waiting rules.
2. Find a visible official action such as `PDF`, `Download PDF`, `Full Text PDF`, or an equivalent accessible label. Inspect current role/element type instead of assuming a fixed selector.
3. Click the normal visible control. Handle either a download event or a new PDF tab. Wait for the download to finish before moving to the next paper.
4. Validate and move the completed file to the destination, then record the landing URL, observed download action, local path, checksum, byte size, page count, and completion time.
5. If the page requires login, let the user complete legitimate institutional/publisher login and resume the same Browser session. If it shows a paywall without authorization, CAPTCHA, DRM barrier, or access denial, stop that item and mark `AUTH_REQUIRED` or `SOURCE_BLOCKED`; never bypass it.

## Failure and retry behavior

- Wait before retrying a blank or slow landing/download page. One reload and one normal re-click are reasonable for a transient failure.
- Do not repeatedly hammer a publisher. Respect visible retry guidance and back off on 429/503.
- An HTML login page saved with a `.pdf` name is a failed download, not success.
- Keep failed items in a resumable audit queue with the exact landing URL and reason. Continue other independent items unless the source presents a venue-wide access barrier.

## Database update

After validation, write or stage a `work_location`/equivalent record linked to the canonical work and source item. Do not change bibliographic fields merely because the PDF filename or embedded metadata differs; queue genuine conflicts for review.

Report requested, downloaded, already present, unavailable, authentication-required, blocked, and invalid-file counts, plus the destination and checkpoint path.
