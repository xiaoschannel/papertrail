import type { components } from './schema'

/** Short names for the generated API models used across pages (source: `api/schemas.py`). */
type Schemas = components['schemas']

export type VizRecord = Schemas['VizRecord']
/** A totals row: grouped by brand (`merchant_group`) or by exact name (`name`). */
export type MerchantTotals = Schemas['BrandTotals'] | Schemas['NameTotals']

/** The label column of a totals row: `merchant_group` when grouped by brand, `name` otherwise. */
export const totalsLabel = (row: MerchantTotals): string => ('merchant_group' in row ? row.merchant_group : row.name)
