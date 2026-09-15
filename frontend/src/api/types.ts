import type { components } from './schema'

/** Short names for the generated API models used across pages (source: `api/schemas.py`). */
type Schemas = components['schemas']

export type VizRecord = Schemas['VizRecord']
/** A totals row: grouped by brand (`merchant_group`) or by exact name (`name`). */
export type MerchantTotals = Schemas['BrandTotals'] | Schemas['NameTotals']

export type AppConfig = Schemas['AppConfig']
export type ReviewQueue = Schemas['ReviewQueue']
export type ReviewSummary = Schemas['ReviewSummary']
export type ReviewDocument = Schemas['ReviewDocument']
export type ReviewPage = Schemas['ReviewPage']
export type FieldBox = Schemas['FieldBoxOut']
export type Draft = Schemas['DraftIn']
export type DecisionIn = Schemas['DecisionIn']
export type Verdict = DecisionIn['verdict']
export type DocumentType = Draft['document_type']
export type HintsResponse = Schemas['HintsResponse']

export type Job = Schemas['JobOut']
export type Batch = Schemas['BatchOut']
export type IndexStatus = Schemas['IndexStatus']
export type ConfirmIndexIn = Schemas['ConfirmIndexIn']
export type Grouping = Schemas['GroupingOut']
export type GroupingPage = Schemas['GroupingPageOut']
export type TopPoints = Schemas['RotateIn']['top_points']
export type OcrStatus = Schemas['OcrStatus']
export type StartOcrIn = Schemas['StartOcrIn']
export type ParseStatus = Schemas['ParseStatus']
export type StartParseIn = Schemas['StartParseIn']
export type ArchiveStatus = Schemas['ArchiveStatus']

/** The label column of a totals row: `merchant_group` when grouped by brand, `name` otherwise. */
export const totalsLabel = (row: MerchantTotals): string => ('merchant_group' in row ? row.merchant_group : row.name)
