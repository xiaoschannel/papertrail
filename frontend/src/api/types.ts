import type { components } from './schema'

/** Short names for the generated API models used across pages (source: `api/schemas.py`). */
type Schemas = components['schemas']

export type VizRecord = Schemas['VizRecord']
/** A totals row: grouped by brand (`merchant_group`) or by exact name (`name`). */
export type MerchantTotals = Schemas['BrandTotals'] | Schemas['NameTotals']

export type AppConfig = Schemas['AppConfig']
export type Shortcuts = Schemas['Shortcuts']
export type ReviewQueue = Schemas['ReviewQueue']
export type ReviewSummary = Schemas['ReviewSummary']
export type ReviewDocument = Schemas['ReviewDocument']
export type ReviewPage = Schemas['ReviewPage']
export type FieldBox = Schemas['FieldBoxOut']
export type BoxRect = Schemas['BoxRect']
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
export type Slicing = Schemas['SlicingOut']
export type SlicingSheet = Schemas['SlicingSheetOut']
export type SheetGrid = Schemas['SheetGrid']
export type SlicePlan = Schemas['SlicePlanOut']
export type OcrStatus = Schemas['OcrStatus']
export type StartOcrIn = Schemas['StartOcrIn']
export type ParseStatus = Schemas['ParseStatus']
export type StartParseIn = Schemas['StartParseIn']
export type ArchiveStatus = Schemas['ArchiveStatus']
export type ConfigOptions = Schemas['ConfigOptions']
export type ReceiptEditIn = Schemas['ReceiptEditIn']
export type LocationCount = Schemas['LocationCount']
export type Brand = Schemas['BrandOut']
export type BrandIn = Schemas['BrandIn']
export type PrefixSuggestion = Schemas['PrefixSuggestionOut']
export type DedupeCluster = Schemas['DedupeCluster']
export type ContextScan = Schemas['ContextScanOut']
export type DedupeMember = Schemas['DedupeMember']
export type KeptPair = Schemas['KeptPair']
export type DedupeOut = Schemas['DedupeOut']
export type NameGroup = Schemas['NameGroupOut']
export type MergePreview = Schemas['MergeOut']
export type MarkedDocument = Schemas['MarkedDocumentOut']
export type WorkshopReprocessIn = Schemas['WorkshopReprocessIn']
export type WorkshopDecisionIn = Schemas['WorkshopDecisionIn']
/** The scan treatment, without the bits that say which document and models to use. */
export type Enhancement = Omit<WorkshopReprocessIn, 'key' | 'ocr_model' | 'extractor'>
export type SanityReport = Schemas['SanityOut']
export type BatchSanity = Schemas['BatchSanityOut']
export type IndexAudit = Schemas['IndexAuditOut']
export type ExperimentOptions = Schemas['ExperimentOut']
export type ExperimentRun = Schemas['ExperimentRunOut']
/** The Workshop's treatment plus denoising, which only the Experiment bench offers. */
export type ExperimentTreatment = Schemas['ExperimentTreatmentIn']
export type ExperimentOcrIn = Schemas['ExperimentOcrIn']
export type ExperimentParseIn = Schemas['ExperimentParseIn']

/** The label column of a totals row: `merchant_group` when grouped by brand, `name` otherwise. */
export const totalsLabel = (row: MerchantTotals): string => ('merchant_group' in row ? row.merchant_group : row.name)
