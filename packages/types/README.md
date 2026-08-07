# @miru/types

Generated TypeScript types from the API's OpenAPI schema.

Empty at M1 on purpose: there is one endpoint shape and it is still moving, so
`apps/web/lib/api.ts` hand-writes it. Generation lands in M2, once the metadata
module fixes the `Series` / `Episode` shapes:

```bash
npx openapi-typescript http://localhost:8000/openapi.json -o packages/types/api.d.ts
```
