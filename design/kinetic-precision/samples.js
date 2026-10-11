// Illustrative fixtures only. They are never sent to an execution service.
export const samples = [
  {
    id:'payment',name:'Make payment retries safe',repo:'payment-service',status:'Needs approval',number:'024',
    files:['confirm.py','test_confirm.py'],directory:'src/payments',
    summary:'One request. One payment.',
    description:'The implementation reuses the original result for duplicate requests. Two scoped files changed; nothing has been committed.',
    failingCheck:'duplicate request returns the original receipt',
    failureOutput:'FAIL duplicate request returns the original receipt\nExpected: the first receipt\nReceived: a second capture\n\nNo commit or pull request was created.',
    source:[['context','from .store import payments, receipts'],['context',''],['context','async def confirm_payment(request):'],['context','    key = request.idempotency_key'],['remove','    return await payments.capture(request.payment_id)'],['add','    async with payments.transaction() as tx:'],['add','        existing = await tx.receipts.find_for_update(key)'],['add','        if existing is not None:'],['add','            return existing'],['add','        receipt = await tx.capture(request.payment_id)'],['add','        await tx.receipts.save(key, receipt)'],['add','        return receipt']],
    tests:[['context','class ConfirmationTests(IsolatedAsyncioTestCase):'],['add','    async def test_duplicate_request_returns_receipt(self):'],['add','        first = await confirm_payment(request)'],['add','        second = await confirm_payment(request)'],['add','        self.assertEqual(second, first)'],['add','        self.assertEqual(await captures.count(), 1)']],
  },
  {
    id:'search',name:'Improve search ranking',repo:'search-api',status:'Completed',number:'023',
    files:['ranker.py','test_ranker.py'],directory:'src/search',
    summary:'The right result. First.',
    description:'Results are ordered by score with an explicit stable tie-breaker. Implementation, verification and review passed in this sample.',
    failingCheck:'equal scores retain stable ordering',
    failureOutput:'FAIL equal scores retain stable ordering\nExpected: deterministic result order\nReceived: unstable ties\n\nNo commit or pull request was created.',
    source:[['context','def rank_results(results):'],['remove','    return results'],['add','    return sorted('],['add','        results,'],['add','        key=lambda result: (-result.score, result.id),'],['add','    )']],
    tests:[['context','class RankingTests(TestCase):'],['add','    def test_equal_scores_are_stable(self):'],['add','        first = rank_results(results)'],['add','        second = rank_results(reversed(results))'],['add','        self.assertEqual(first, second)']],
  },
  {
    id:'cache',name:'Fix cache invalidation',repo:'edge-worker',status:'Verification failed',number:'022',
    files:['cache.py','test_cache.py'],directory:'src/cache',
    summary:'Fresh data. Every read.',
    description:'The sample changes invalidate a cache key after a write. The verification caught stale reads, so final review has not run.',
    failingCheck:'cache keys / invalidation on write',
    failureOutput:'FAIL cache invalidation on write\nExpected: fresh entry after mutation\nReceived: previous cached value\n\nNo commit or pull request was created.',
    source:[['context','async def save_entry(key, value):'],['context','    await storage.write(key, value)'],['add','    await cache.invalidate(key)'],['context','    return value']],
    tests:[['context','class CacheTests(IsolatedAsyncioTestCase):'],['add','    async def test_write_invalidates_entry(self):'],['add','        await save_entry("example", "fresh")'],['add','        result = await read_entry("example")'],['add','        self.assertEqual(result, "fresh")']],
  },
];
