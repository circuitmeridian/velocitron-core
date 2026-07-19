// Generated from VelocitronPetriNet.g4 by ANTLR 4.13.2
// noinspection ES6UnusedImports,JSUnusedGlobalSymbols,JSUnusedLocalSymbols

import {
	ATN,
	ATNDeserializer, DecisionState, DFA, FailedPredicateException,
	RecognitionException, NoViableAltException, BailErrorStrategy,
	Parser, ParserATNSimulator,
	RuleContext, ParserRuleContext, PredictionMode, PredictionContextCache,
	TerminalNode, RuleNode,
	Token, TokenStream,
	Interval, IntervalSet
} from 'antlr4';
import VelocitronPetriNetVisitor from "./VelocitronPetriNetVisitor.js";

// for running tests with parameters, TODO: discuss strategy for typed parameters in CI
// eslint-disable-next-line no-unused-vars
type int = number;

export default class VelocitronPetriNetParser extends Parser {
	public static readonly NET = 1;
	public static readonly COMPOSITION = 2;
	public static readonly USE = 3;
	public static readonly AS = 4;
	public static readonly WIRE = 5;
	public static readonly PORT = 6;
	public static readonly INPUT = 7;
	public static readonly OUTPUT = 8;
	public static readonly HANDLER = 9;
	public static readonly GUARD = 10;
	public static readonly ORDER = 11;
	public static readonly TIMER = 12;
	public static readonly CLOCK = 13;
	public static readonly BIND = 14;
	public static readonly MATURITY = 15;
	public static readonly PRIORITY = 16;
	public static readonly ACCEPTS = 17;
	public static readonly CAPACITY_PER_COLOR_KEY = 18;
	public static readonly WEIGHT = 19;
	public static readonly DATA = 20;
	public static readonly PREDICATE = 21;
	public static readonly CEL = 22;
	public static readonly CORRELATE = 23;
	public static readonly MARKING = 24;
	public static readonly INITIAL = 25;
	public static readonly DESCRIPTION = 26;
	public static readonly ANNOTATION = 27;
	public static readonly EXTENSIONS = 28;
	public static readonly VIEW = 29;
	public static readonly POSITION = 30;
	public static readonly ROUTE = 31;
	public static readonly AT_KEYWORD = 32;
	public static readonly ORTHOGONAL = 33;
	public static readonly TRUE = 34;
	public static readonly FALSE = 35;
	public static readonly NULL = 36;
	public static readonly AT = 37;
	public static readonly LPAREN = 38;
	public static readonly RPAREN = 39;
	public static readonly LBRACK = 40;
	public static readonly RBRACK = 41;
	public static readonly LBRACE = 42;
	public static readonly RBRACE = 43;
	public static readonly HYPHEN = 44;
	public static readonly READ_ARROW = 45;
	public static readonly INHIBIT_ARROW = 46;
	public static readonly ARROW = 47;
	public static readonly LEFT_ARROW = 48;
	public static readonly DOLLAR = 49;
	public static readonly STAR = 50;
	public static readonly COLON = 51;
	public static readonly COMMA = 52;
	public static readonly DOT = 53;
	public static readonly POSITIVE_INTEGER = 54;
	public static readonly ZERO = 55;
	public static readonly NUMBER = 56;
	public static readonly STRING = 57;
	public static readonly IDENT = 58;
	public static readonly LINE_COMMENT = 59;
	public static readonly BLOCK_COMMENT = 60;
	public static readonly WS = 61;
	public static readonly CRLF = 62;
	public static readonly UNSUPPORTED = 63;
	public static override readonly EOF = Token.EOF;
	public static readonly RULE_document = 0;
	public static readonly RULE_netHeader = 1;
	public static readonly RULE_compositionHeader = 2;
	public static readonly RULE_compositionUse = 3;
	public static readonly RULE_compositionWire = 4;
	public static readonly RULE_chain = 5;
	public static readonly RULE_chainNode = 6;
	public static readonly RULE_arcSegment = 7;
	public static readonly RULE_arcOperator = 8;
	public static readonly RULE_additionalChain = 9;
	public static readonly RULE_chainHandle = 10;
	public static readonly RULE_place = 11;
	public static readonly RULE_transition = 12;
	public static readonly RULE_placeDeclaration = 13;
	public static readonly RULE_transitionDeclaration = 14;
	public static readonly RULE_placePort = 15;
	public static readonly RULE_portDirection = 16;
	public static readonly RULE_transitionHandler = 17;
	public static readonly RULE_additionalTransitionHandler = 18;
	public static readonly RULE_transitionGuard = 19;
	public static readonly RULE_transitionTimer = 20;
	public static readonly RULE_transitionTimerBind = 21;
	public static readonly RULE_transitionTimerMaturity = 22;
	public static readonly RULE_timerBindName = 23;
	public static readonly RULE_transitionPriority = 24;
	public static readonly RULE_transitionOrder = 25;
	public static readonly RULE_chainOrder = 26;
	public static readonly RULE_placeAccepts = 27;
	public static readonly RULE_placeCapacity = 28;
	public static readonly RULE_arcWeight = 29;
	public static readonly RULE_arcData = 30;
	public static readonly RULE_arcPredicate = 31;
	public static readonly RULE_arcCorrelate = 32;
	public static readonly RULE_predicateKind = 33;
	public static readonly RULE_positiveInteger = 34;
	public static readonly RULE_nonnegativeInteger = 35;
	public static readonly RULE_initialMarking = 36;
	public static readonly RULE_additionalInitialMarking = 37;
	public static readonly RULE_namedMarking = 38;
	public static readonly RULE_markingValue = 39;
	public static readonly RULE_metadataDescription = 40;
	public static readonly RULE_metadataAnnotation = 41;
	public static readonly RULE_metadataTarget = 42;
	public static readonly RULE_viewPosition = 43;
	public static readonly RULE_viewRoute = 44;
	public static readonly RULE_viewTarget = 45;
	public static readonly RULE_extensions = 46;
	public static readonly RULE_templateDefinition = 47;
	public static readonly RULE_additionalTemplateDefinition = 48;
	public static readonly RULE_templateReference = 49;
	public static readonly RULE_color = 50;
	public static readonly RULE_name = 51;
	public static readonly RULE_jsonValue = 52;
	public static readonly RULE_jsonObject = 53;
	public static readonly RULE_jsonArray = 54;
	public static readonly literalNames: (string | null)[] = [ null, "'net'", 
                                                            "'composition'", 
                                                            "'use'", "'as'", 
                                                            "'wire'", "'port'", 
                                                            "'input'", "'output'", 
                                                            "'handler'", 
                                                            "'guard'", "'order'", 
                                                            "'timer'", "'clock'", 
                                                            "'bind'", "'maturity'", 
                                                            "'priority'", 
                                                            "'accepts'", 
                                                            "'capacityPerColorKey'", 
                                                            "'weight'", 
                                                            "'data'", "'predicate'", 
                                                            "'cel'", "'correlate'", 
                                                            "'marking'", 
                                                            "'initial'", 
                                                            "'description'", 
                                                            "'annotation'", 
                                                            "'extensions'", 
                                                            "'view'", "'position'", 
                                                            "'route'", "'at'", 
                                                            "'orthogonal'", 
                                                            "'true'", "'false'", 
                                                            "'null'", "'@'", 
                                                            "'('", "')'", 
                                                            "'['", "']'", 
                                                            "'{'", "'}'", 
                                                            "'-'", "'->?'", 
                                                            "'->0'", "'->'", 
                                                            "'<-'", "'$'", 
                                                            "'*'", "':'", 
                                                            "','", "'.'", 
                                                            null, "'0'", 
                                                            null, null, 
                                                            null, null, 
                                                            null, null, 
                                                            "'\\r\\n'" ];
	public static readonly symbolicNames: (string | null)[] = [ null, "NET", 
                                                             "COMPOSITION", 
                                                             "USE", "AS", 
                                                             "WIRE", "PORT", 
                                                             "INPUT", "OUTPUT", 
                                                             "HANDLER", 
                                                             "GUARD", "ORDER", 
                                                             "TIMER", "CLOCK", 
                                                             "BIND", "MATURITY", 
                                                             "PRIORITY", 
                                                             "ACCEPTS", 
                                                             "CAPACITY_PER_COLOR_KEY", 
                                                             "WEIGHT", "DATA", 
                                                             "PREDICATE", 
                                                             "CEL", "CORRELATE", 
                                                             "MARKING", 
                                                             "INITIAL", 
                                                             "DESCRIPTION", 
                                                             "ANNOTATION", 
                                                             "EXTENSIONS", 
                                                             "VIEW", "POSITION", 
                                                             "ROUTE", "AT_KEYWORD", 
                                                             "ORTHOGONAL", 
                                                             "TRUE", "FALSE", 
                                                             "NULL", "AT", 
                                                             "LPAREN", "RPAREN", 
                                                             "LBRACK", "RBRACK", 
                                                             "LBRACE", "RBRACE", 
                                                             "HYPHEN", "READ_ARROW", 
                                                             "INHIBIT_ARROW", 
                                                             "ARROW", "LEFT_ARROW", 
                                                             "DOLLAR", "STAR", 
                                                             "COLON", "COMMA", 
                                                             "DOT", "POSITIVE_INTEGER", 
                                                             "ZERO", "NUMBER", 
                                                             "STRING", "IDENT", 
                                                             "LINE_COMMENT", 
                                                             "BLOCK_COMMENT", 
                                                             "WS", "CRLF", 
                                                             "UNSUPPORTED" ];
	// tslint:disable:no-trailing-whitespace
	public static readonly ruleNames: string[] = [
		"document", "netHeader", "compositionHeader", "compositionUse", "compositionWire", 
		"chain", "chainNode", "arcSegment", "arcOperator", "additionalChain", 
		"chainHandle", "place", "transition", "placeDeclaration", "transitionDeclaration", 
		"placePort", "portDirection", "transitionHandler", "additionalTransitionHandler", 
		"transitionGuard", "transitionTimer", "transitionTimerBind", "transitionTimerMaturity", 
		"timerBindName", "transitionPriority", "transitionOrder", "chainOrder", 
		"placeAccepts", "placeCapacity", "arcWeight", "arcData", "arcPredicate", 
		"arcCorrelate", "predicateKind", "positiveInteger", "nonnegativeInteger", 
		"initialMarking", "additionalInitialMarking", "namedMarking", "markingValue", 
		"metadataDescription", "metadataAnnotation", "metadataTarget", "viewPosition", 
		"viewRoute", "viewTarget", "extensions", "templateDefinition", "additionalTemplateDefinition", 
		"templateReference", "color", "name", "jsonValue", "jsonObject", "jsonArray",
	];
	public get grammarFileName(): string { return "VelocitronPetriNet.g4"; }
	public get literalNames(): (string | null)[] { return VelocitronPetriNetParser.literalNames; }
	public get symbolicNames(): (string | null)[] { return VelocitronPetriNetParser.symbolicNames; }
	public get ruleNames(): string[] { return VelocitronPetriNetParser.ruleNames; }
	public get serializedATN(): number[] { return VelocitronPetriNetParser._serializedATN; }

	protected createFailedPredicateException(predicate?: string, message?: string): FailedPredicateException {
		return new FailedPredicateException(this, predicate, message);
	}

	constructor(input: TokenStream) {
		super(input);
		this._interp = new ParserATNSimulator(this, VelocitronPetriNetParser._ATN, VelocitronPetriNetParser.DecisionsToDFA, new PredictionContextCache());
	}
	// @RuleVersion(0)
	public document(): DocumentContext {
		let localctx: DocumentContext = new DocumentContext(this, this._ctx, this.state);
		this.enterRule(localctx, 0, VelocitronPetriNetParser.RULE_document);
		let _la: number;
		try {
			this.state = 155;
			this._errHandler.sync(this);
			switch (this._input.LA(1)) {
			case -1:
			case 1:
			case 24:
			case 28:
			case 29:
			case 37:
			case 38:
			case 40:
			case 49:
				this.enterOuterAlt(localctx, 1);
				{
				this.state = 111;
				this._errHandler.sync(this);
				switch ( this._interp.adaptivePredict(this._input, 0, this._ctx) ) {
				case 1:
					{
					this.state = 110;
					this.netHeader();
					}
					break;
				}
				this.state = 141;
				this._errHandler.sync(this);
				_la = this._input.LA(1);
				while ((((_la) & ~0x1F) === 0 && ((1 << _la) & 822083586) !== 0) || ((((_la - 37)) & ~0x1F) === 0 && ((1 << (_la - 37)) & 4107) !== 0)) {
					{
					this.state = 139;
					this._errHandler.sync(this);
					switch ( this._interp.adaptivePredict(this._input, 1, this._ctx) ) {
					case 1:
						{
						this.state = 113;
						this.chain();
						}
						break;
					case 2:
						{
						this.state = 114;
						this.placeDeclaration();
						}
						break;
					case 3:
						{
						this.state = 115;
						this.transitionDeclaration();
						}
						break;
					case 4:
						{
						this.state = 116;
						this.transitionHandler();
						}
						break;
					case 5:
						{
						this.state = 117;
						this.transitionGuard();
						}
						break;
					case 6:
						{
						this.state = 118;
						this.transitionTimer();
						}
						break;
					case 7:
						{
						this.state = 119;
						this.transitionTimerBind();
						}
						break;
					case 8:
						{
						this.state = 120;
						this.transitionTimerMaturity();
						}
						break;
					case 9:
						{
						this.state = 121;
						this.transitionPriority();
						}
						break;
					case 10:
						{
						this.state = 122;
						this.transitionOrder();
						}
						break;
					case 11:
						{
						this.state = 123;
						this.chainOrder();
						}
						break;
					case 12:
						{
						this.state = 124;
						this.placePort();
						}
						break;
					case 13:
						{
						this.state = 125;
						this.placeAccepts();
						}
						break;
					case 14:
						{
						this.state = 126;
						this.placeCapacity();
						}
						break;
					case 15:
						{
						this.state = 127;
						this.arcWeight();
						}
						break;
					case 16:
						{
						this.state = 128;
						this.arcData();
						}
						break;
					case 17:
						{
						this.state = 129;
						this.arcPredicate();
						}
						break;
					case 18:
						{
						this.state = 130;
						this.arcCorrelate();
						}
						break;
					case 19:
						{
						this.state = 131;
						this.initialMarking();
						}
						break;
					case 20:
						{
						this.state = 132;
						this.templateDefinition();
						}
						break;
					case 21:
						{
						this.state = 133;
						this.namedMarking();
						}
						break;
					case 22:
						{
						this.state = 134;
						this.metadataDescription();
						}
						break;
					case 23:
						{
						this.state = 135;
						this.metadataAnnotation();
						}
						break;
					case 24:
						{
						this.state = 136;
						this.viewPosition();
						}
						break;
					case 25:
						{
						this.state = 137;
						this.viewRoute();
						}
						break;
					case 26:
						{
						this.state = 138;
						this.extensions();
						}
						break;
					}
					}
					this.state = 143;
					this._errHandler.sync(this);
					_la = this._input.LA(1);
				}
				this.state = 144;
				this.match(VelocitronPetriNetParser.EOF);
				}
				break;
			case 2:
				this.enterOuterAlt(localctx, 2);
				{
				this.state = 145;
				this.compositionHeader();
				this.state = 150;
				this._errHandler.sync(this);
				_la = this._input.LA(1);
				while (_la===3 || _la===5) {
					{
					this.state = 148;
					this._errHandler.sync(this);
					switch (this._input.LA(1)) {
					case 3:
						{
						this.state = 146;
						this.compositionUse();
						}
						break;
					case 5:
						{
						this.state = 147;
						this.compositionWire();
						}
						break;
					default:
						throw new NoViableAltException(this);
					}
					}
					this.state = 152;
					this._errHandler.sync(this);
					_la = this._input.LA(1);
				}
				this.state = 153;
				this.match(VelocitronPetriNetParser.EOF);
				}
				break;
			default:
				throw new NoViableAltException(this);
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public netHeader(): NetHeaderContext {
		let localctx: NetHeaderContext = new NetHeaderContext(this, this._ctx, this.state);
		this.enterRule(localctx, 2, VelocitronPetriNetParser.RULE_netHeader);
		let _la: number;
		try {
			this.enterOuterAlt(localctx, 1);
			{
			this.state = 157;
			this.match(VelocitronPetriNetParser.NET);
			this.state = 158;
			this.name();
			this.state = 160;
			this._errHandler.sync(this);
			_la = this._input.LA(1);
			if (_la===57) {
				{
				this.state = 159;
				this.match(VelocitronPetriNetParser.STRING);
				}
			}

			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public compositionHeader(): CompositionHeaderContext {
		let localctx: CompositionHeaderContext = new CompositionHeaderContext(this, this._ctx, this.state);
		this.enterRule(localctx, 4, VelocitronPetriNetParser.RULE_compositionHeader);
		try {
			this.enterOuterAlt(localctx, 1);
			{
			this.state = 162;
			this.match(VelocitronPetriNetParser.COMPOSITION);
			this.state = 163;
			this.name();
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public compositionUse(): CompositionUseContext {
		let localctx: CompositionUseContext = new CompositionUseContext(this, this._ctx, this.state);
		this.enterRule(localctx, 6, VelocitronPetriNetParser.RULE_compositionUse);
		try {
			this.enterOuterAlt(localctx, 1);
			{
			this.state = 165;
			this.match(VelocitronPetriNetParser.USE);
			this.state = 166;
			this.match(VelocitronPetriNetParser.STRING);
			this.state = 167;
			this.match(VelocitronPetriNetParser.AS);
			this.state = 168;
			this.match(VelocitronPetriNetParser.IDENT);
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public compositionWire(): CompositionWireContext {
		let localctx: CompositionWireContext = new CompositionWireContext(this, this._ctx, this.state);
		this.enterRule(localctx, 8, VelocitronPetriNetParser.RULE_compositionWire);
		try {
			this.enterOuterAlt(localctx, 1);
			{
			this.state = 170;
			this.match(VelocitronPetriNetParser.WIRE);
			this.state = 171;
			this.match(VelocitronPetriNetParser.IDENT);
			this.state = 172;
			this.match(VelocitronPetriNetParser.DOT);
			this.state = 173;
			this.place();
			this.state = 174;
			this.match(VelocitronPetriNetParser.ARROW);
			this.state = 175;
			this.match(VelocitronPetriNetParser.IDENT);
			this.state = 176;
			this.match(VelocitronPetriNetParser.DOT);
			this.state = 177;
			this.place();
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public chain(): ChainContext {
		let localctx: ChainContext = new ChainContext(this, this._ctx, this.state);
		this.enterRule(localctx, 10, VelocitronPetriNetParser.RULE_chain);
		let _la: number;
		try {
			this.enterOuterAlt(localctx, 1);
			{
			this.state = 180;
			this._errHandler.sync(this);
			_la = this._input.LA(1);
			if (_la===37) {
				{
				this.state = 179;
				this.chainHandle();
				}
			}

			this.state = 182;
			this.chainNode();
			this.state = 186;
			this._errHandler.sync(this);
			_la = this._input.LA(1);
			do {
				{
				{
				this.state = 183;
				this.arcSegment();
				this.state = 184;
				this.chainNode();
				}
				}
				this.state = 188;
				this._errHandler.sync(this);
				_la = this._input.LA(1);
			} while (((((_la - 44)) & ~0x1F) === 0 && ((1 << (_la - 44)) & 15) !== 0));
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public chainNode(): ChainNodeContext {
		let localctx: ChainNodeContext = new ChainNodeContext(this, this._ctx, this.state);
		this.enterRule(localctx, 12, VelocitronPetriNetParser.RULE_chainNode);
		try {
			this.state = 192;
			this._errHandler.sync(this);
			switch (this._input.LA(1)) {
			case 38:
				this.enterOuterAlt(localctx, 1);
				{
				this.state = 190;
				this.place();
				}
				break;
			case 40:
				this.enterOuterAlt(localctx, 2);
				{
				this.state = 191;
				this.transition();
				}
				break;
			default:
				throw new NoViableAltException(this);
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public arcSegment(): ArcSegmentContext {
		let localctx: ArcSegmentContext = new ArcSegmentContext(this, this._ctx, this.state);
		this.enterRule(localctx, 14, VelocitronPetriNetParser.RULE_arcSegment);
		let _la: number;
		try {
			this.enterOuterAlt(localctx, 1);
			{
			this.state = 196;
			this._errHandler.sync(this);
			_la = this._input.LA(1);
			if (_la===44) {
				{
				this.state = 194;
				this.match(VelocitronPetriNetParser.HYPHEN);
				this.state = 195;
				this.color();
				}
			}

			this.state = 198;
			this.arcOperator();
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public arcOperator(): ArcOperatorContext {
		let localctx: ArcOperatorContext = new ArcOperatorContext(this, this._ctx, this.state);
		this.enterRule(localctx, 16, VelocitronPetriNetParser.RULE_arcOperator);
		let _la: number;
		try {
			this.enterOuterAlt(localctx, 1);
			{
			this.state = 200;
			_la = this._input.LA(1);
			if(!(((((_la - 45)) & ~0x1F) === 0 && ((1 << (_la - 45)) & 7) !== 0))) {
			this._errHandler.recoverInline(this);
			}
			else {
				this._errHandler.reportMatch(this);
			    this.consume();
			}
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public additionalChain(): AdditionalChainContext {
		let localctx: AdditionalChainContext = new AdditionalChainContext(this, this._ctx, this.state);
		this.enterRule(localctx, 18, VelocitronPetriNetParser.RULE_additionalChain);
		try {
			this.enterOuterAlt(localctx, 1);
			{
			this.state = 202;
			this.chain();
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public chainHandle(): ChainHandleContext {
		let localctx: ChainHandleContext = new ChainHandleContext(this, this._ctx, this.state);
		this.enterRule(localctx, 20, VelocitronPetriNetParser.RULE_chainHandle);
		try {
			this.enterOuterAlt(localctx, 1);
			{
			this.state = 204;
			this.match(VelocitronPetriNetParser.AT);
			this.state = 205;
			this.name();
			this.state = 206;
			this.match(VelocitronPetriNetParser.COLON);
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public place(): PlaceContext {
		let localctx: PlaceContext = new PlaceContext(this, this._ctx, this.state);
		this.enterRule(localctx, 22, VelocitronPetriNetParser.RULE_place);
		try {
			this.enterOuterAlt(localctx, 1);
			{
			this.state = 208;
			this.match(VelocitronPetriNetParser.LPAREN);
			this.state = 209;
			this.name();
			this.state = 210;
			this.match(VelocitronPetriNetParser.RPAREN);
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public transition(): TransitionContext {
		let localctx: TransitionContext = new TransitionContext(this, this._ctx, this.state);
		this.enterRule(localctx, 24, VelocitronPetriNetParser.RULE_transition);
		try {
			this.enterOuterAlt(localctx, 1);
			{
			this.state = 212;
			this.match(VelocitronPetriNetParser.LBRACK);
			this.state = 213;
			this.name();
			this.state = 214;
			this.match(VelocitronPetriNetParser.RBRACK);
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public placeDeclaration(): PlaceDeclarationContext {
		let localctx: PlaceDeclarationContext = new PlaceDeclarationContext(this, this._ctx, this.state);
		this.enterRule(localctx, 26, VelocitronPetriNetParser.RULE_placeDeclaration);
		try {
			this.enterOuterAlt(localctx, 1);
			{
			this.state = 216;
			this.place();
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public transitionDeclaration(): TransitionDeclarationContext {
		let localctx: TransitionDeclarationContext = new TransitionDeclarationContext(this, this._ctx, this.state);
		this.enterRule(localctx, 28, VelocitronPetriNetParser.RULE_transitionDeclaration);
		try {
			this.enterOuterAlt(localctx, 1);
			{
			this.state = 218;
			this.transition();
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public placePort(): PlacePortContext {
		let localctx: PlacePortContext = new PlacePortContext(this, this._ctx, this.state);
		this.enterRule(localctx, 30, VelocitronPetriNetParser.RULE_placePort);
		try {
			this.enterOuterAlt(localctx, 1);
			{
			this.state = 220;
			this.place();
			this.state = 221;
			this.match(VelocitronPetriNetParser.PORT);
			this.state = 222;
			this.portDirection();
			this.state = 223;
			this.color();
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public portDirection(): PortDirectionContext {
		let localctx: PortDirectionContext = new PortDirectionContext(this, this._ctx, this.state);
		this.enterRule(localctx, 32, VelocitronPetriNetParser.RULE_portDirection);
		let _la: number;
		try {
			this.enterOuterAlt(localctx, 1);
			{
			this.state = 225;
			_la = this._input.LA(1);
			if(!(_la===7 || _la===8)) {
			this._errHandler.recoverInline(this);
			}
			else {
				this._errHandler.reportMatch(this);
			    this.consume();
			}
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public transitionHandler(): TransitionHandlerContext {
		let localctx: TransitionHandlerContext = new TransitionHandlerContext(this, this._ctx, this.state);
		this.enterRule(localctx, 34, VelocitronPetriNetParser.RULE_transitionHandler);
		try {
			this.enterOuterAlt(localctx, 1);
			{
			this.state = 227;
			this.transition();
			this.state = 228;
			this.match(VelocitronPetriNetParser.HANDLER);
			this.state = 229;
			this.match(VelocitronPetriNetParser.STRING);
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public additionalTransitionHandler(): AdditionalTransitionHandlerContext {
		let localctx: AdditionalTransitionHandlerContext = new AdditionalTransitionHandlerContext(this, this._ctx, this.state);
		this.enterRule(localctx, 36, VelocitronPetriNetParser.RULE_additionalTransitionHandler);
		try {
			this.enterOuterAlt(localctx, 1);
			{
			this.state = 231;
			this.transitionHandler();
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public transitionGuard(): TransitionGuardContext {
		let localctx: TransitionGuardContext = new TransitionGuardContext(this, this._ctx, this.state);
		this.enterRule(localctx, 38, VelocitronPetriNetParser.RULE_transitionGuard);
		try {
			this.enterOuterAlt(localctx, 1);
			{
			this.state = 233;
			this.transition();
			this.state = 234;
			this.match(VelocitronPetriNetParser.GUARD);
			this.state = 235;
			this.match(VelocitronPetriNetParser.STRING);
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public transitionTimer(): TransitionTimerContext {
		let localctx: TransitionTimerContext = new TransitionTimerContext(this, this._ctx, this.state);
		this.enterRule(localctx, 40, VelocitronPetriNetParser.RULE_transitionTimer);
		try {
			this.enterOuterAlt(localctx, 1);
			{
			this.state = 237;
			this.transition();
			this.state = 238;
			this.match(VelocitronPetriNetParser.TIMER);
			this.state = 239;
			this.match(VelocitronPetriNetParser.CLOCK);
			this.state = 240;
			this.place();
			this.state = 241;
			this.match(VelocitronPetriNetParser.CEL);
			this.state = 242;
			this.match(VelocitronPetriNetParser.STRING);
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public transitionTimerBind(): TransitionTimerBindContext {
		let localctx: TransitionTimerBindContext = new TransitionTimerBindContext(this, this._ctx, this.state);
		this.enterRule(localctx, 42, VelocitronPetriNetParser.RULE_transitionTimerBind);
		try {
			this.enterOuterAlt(localctx, 1);
			{
			this.state = 244;
			this.transition();
			this.state = 245;
			this.match(VelocitronPetriNetParser.TIMER);
			this.state = 246;
			this.match(VelocitronPetriNetParser.BIND);
			this.state = 247;
			this.timerBindName();
			this.state = 248;
			this.place();
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public transitionTimerMaturity(): TransitionTimerMaturityContext {
		let localctx: TransitionTimerMaturityContext = new TransitionTimerMaturityContext(this, this._ctx, this.state);
		this.enterRule(localctx, 44, VelocitronPetriNetParser.RULE_transitionTimerMaturity);
		try {
			this.enterOuterAlt(localctx, 1);
			{
			this.state = 250;
			this.transition();
			this.state = 251;
			this.match(VelocitronPetriNetParser.TIMER);
			this.state = 252;
			this.match(VelocitronPetriNetParser.MATURITY);
			this.state = 253;
			this.match(VelocitronPetriNetParser.CEL);
			this.state = 254;
			this.match(VelocitronPetriNetParser.STRING);
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public timerBindName(): TimerBindNameContext {
		let localctx: TimerBindNameContext = new TimerBindNameContext(this, this._ctx, this.state);
		this.enterRule(localctx, 46, VelocitronPetriNetParser.RULE_timerBindName);
		let _la: number;
		try {
			this.enterOuterAlt(localctx, 1);
			{
			this.state = 256;
			_la = this._input.LA(1);
			if(!(_la===13 || _la===58)) {
			this._errHandler.recoverInline(this);
			}
			else {
				this._errHandler.reportMatch(this);
			    this.consume();
			}
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public transitionPriority(): TransitionPriorityContext {
		let localctx: TransitionPriorityContext = new TransitionPriorityContext(this, this._ctx, this.state);
		this.enterRule(localctx, 48, VelocitronPetriNetParser.RULE_transitionPriority);
		try {
			this.enterOuterAlt(localctx, 1);
			{
			this.state = 258;
			this.transition();
			this.state = 259;
			this.match(VelocitronPetriNetParser.PRIORITY);
			this.state = 260;
			this.nonnegativeInteger();
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public transitionOrder(): TransitionOrderContext {
		let localctx: TransitionOrderContext = new TransitionOrderContext(this, this._ctx, this.state);
		this.enterRule(localctx, 50, VelocitronPetriNetParser.RULE_transitionOrder);
		try {
			this.enterOuterAlt(localctx, 1);
			{
			this.state = 262;
			this.transition();
			this.state = 263;
			this.match(VelocitronPetriNetParser.ORDER);
			this.state = 264;
			this.positiveInteger();
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public chainOrder(): ChainOrderContext {
		let localctx: ChainOrderContext = new ChainOrderContext(this, this._ctx, this.state);
		this.enterRule(localctx, 52, VelocitronPetriNetParser.RULE_chainOrder);
		try {
			this.enterOuterAlt(localctx, 1);
			{
			this.state = 266;
			this.match(VelocitronPetriNetParser.AT);
			this.state = 267;
			this.name();
			this.state = 268;
			this.match(VelocitronPetriNetParser.ORDER);
			this.state = 269;
			this.positiveInteger();
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public placeAccepts(): PlaceAcceptsContext {
		let localctx: PlaceAcceptsContext = new PlaceAcceptsContext(this, this._ctx, this.state);
		this.enterRule(localctx, 54, VelocitronPetriNetParser.RULE_placeAccepts);
		let _la: number;
		try {
			this.enterOuterAlt(localctx, 1);
			{
			this.state = 271;
			this.place();
			this.state = 272;
			this.match(VelocitronPetriNetParser.ACCEPTS);
			this.state = 273;
			this.match(VelocitronPetriNetParser.LBRACK);
			this.state = 274;
			this.color();
			this.state = 279;
			this._errHandler.sync(this);
			_la = this._input.LA(1);
			while (_la===52) {
				{
				{
				this.state = 275;
				this.match(VelocitronPetriNetParser.COMMA);
				this.state = 276;
				this.color();
				}
				}
				this.state = 281;
				this._errHandler.sync(this);
				_la = this._input.LA(1);
			}
			this.state = 282;
			this.match(VelocitronPetriNetParser.RBRACK);
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public placeCapacity(): PlaceCapacityContext {
		let localctx: PlaceCapacityContext = new PlaceCapacityContext(this, this._ctx, this.state);
		this.enterRule(localctx, 56, VelocitronPetriNetParser.RULE_placeCapacity);
		try {
			this.enterOuterAlt(localctx, 1);
			{
			this.state = 284;
			this.place();
			this.state = 285;
			this.match(VelocitronPetriNetParser.CAPACITY_PER_COLOR_KEY);
			this.state = 286;
			this.jsonObject();
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public arcWeight(): ArcWeightContext {
		let localctx: ArcWeightContext = new ArcWeightContext(this, this._ctx, this.state);
		this.enterRule(localctx, 58, VelocitronPetriNetParser.RULE_arcWeight);
		try {
			this.enterOuterAlt(localctx, 1);
			{
			this.state = 288;
			this.match(VelocitronPetriNetParser.AT);
			this.state = 289;
			this.name();
			this.state = 290;
			this.match(VelocitronPetriNetParser.WEIGHT);
			this.state = 291;
			this.jsonValue();
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public arcData(): ArcDataContext {
		let localctx: ArcDataContext = new ArcDataContext(this, this._ctx, this.state);
		this.enterRule(localctx, 60, VelocitronPetriNetParser.RULE_arcData);
		try {
			this.state = 304;
			this._errHandler.sync(this);
			switch ( this._interp.adaptivePredict(this._input, 12, this._ctx) ) {
			case 1:
				this.enterOuterAlt(localctx, 1);
				{
				this.state = 293;
				this.match(VelocitronPetriNetParser.AT);
				this.state = 294;
				this.name();
				this.state = 295;
				this.match(VelocitronPetriNetParser.DATA);
				this.state = 296;
				this.match(VelocitronPetriNetParser.CEL);
				this.state = 297;
				this.match(VelocitronPetriNetParser.STRING);
				}
				break;
			case 2:
				this.enterOuterAlt(localctx, 2);
				{
				this.state = 299;
				this.match(VelocitronPetriNetParser.AT);
				this.state = 300;
				this.name();
				this.state = 301;
				this.match(VelocitronPetriNetParser.DATA);
				this.state = 302;
				this.jsonValue();
				}
				break;
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public arcPredicate(): ArcPredicateContext {
		let localctx: ArcPredicateContext = new ArcPredicateContext(this, this._ctx, this.state);
		this.enterRule(localctx, 62, VelocitronPetriNetParser.RULE_arcPredicate);
		try {
			this.enterOuterAlt(localctx, 1);
			{
			this.state = 306;
			this.match(VelocitronPetriNetParser.AT);
			this.state = 307;
			this.name();
			this.state = 308;
			this.match(VelocitronPetriNetParser.PREDICATE);
			this.state = 309;
			this.predicateKind();
			this.state = 310;
			this.match(VelocitronPetriNetParser.STRING);
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public arcCorrelate(): ArcCorrelateContext {
		let localctx: ArcCorrelateContext = new ArcCorrelateContext(this, this._ctx, this.state);
		this.enterRule(localctx, 64, VelocitronPetriNetParser.RULE_arcCorrelate);
		try {
			this.enterOuterAlt(localctx, 1);
			{
			this.state = 312;
			this.match(VelocitronPetriNetParser.AT);
			this.state = 313;
			this.name();
			this.state = 314;
			this.match(VelocitronPetriNetParser.CORRELATE);
			this.state = 315;
			this.match(VelocitronPetriNetParser.CEL);
			this.state = 316;
			this.match(VelocitronPetriNetParser.STRING);
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public predicateKind(): PredicateKindContext {
		let localctx: PredicateKindContext = new PredicateKindContext(this, this._ctx, this.state);
		this.enterRule(localctx, 66, VelocitronPetriNetParser.RULE_predicateKind);
		let _la: number;
		try {
			this.enterOuterAlt(localctx, 1);
			{
			this.state = 318;
			_la = this._input.LA(1);
			if(!(_la===9 || _la===22)) {
			this._errHandler.recoverInline(this);
			}
			else {
				this._errHandler.reportMatch(this);
			    this.consume();
			}
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public positiveInteger(): PositiveIntegerContext {
		let localctx: PositiveIntegerContext = new PositiveIntegerContext(this, this._ctx, this.state);
		this.enterRule(localctx, 68, VelocitronPetriNetParser.RULE_positiveInteger);
		try {
			this.enterOuterAlt(localctx, 1);
			{
			this.state = 320;
			this.match(VelocitronPetriNetParser.POSITIVE_INTEGER);
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public nonnegativeInteger(): NonnegativeIntegerContext {
		let localctx: NonnegativeIntegerContext = new NonnegativeIntegerContext(this, this._ctx, this.state);
		this.enterRule(localctx, 70, VelocitronPetriNetParser.RULE_nonnegativeInteger);
		let _la: number;
		try {
			this.enterOuterAlt(localctx, 1);
			{
			this.state = 322;
			_la = this._input.LA(1);
			if(!(_la===54 || _la===55)) {
			this._errHandler.recoverInline(this);
			}
			else {
				this._errHandler.reportMatch(this);
			    this.consume();
			}
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public initialMarking(): InitialMarkingContext {
		let localctx: InitialMarkingContext = new InitialMarkingContext(this, this._ctx, this.state);
		this.enterRule(localctx, 72, VelocitronPetriNetParser.RULE_initialMarking);
		try {
			this.enterOuterAlt(localctx, 1);
			{
			this.state = 324;
			this.match(VelocitronPetriNetParser.MARKING);
			this.state = 325;
			this.match(VelocitronPetriNetParser.INITIAL);
			this.state = 326;
			this.place();
			this.state = 327;
			this.match(VelocitronPetriNetParser.LEFT_ARROW);
			this.state = 328;
			this.markingValue();
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public additionalInitialMarking(): AdditionalInitialMarkingContext {
		let localctx: AdditionalInitialMarkingContext = new AdditionalInitialMarkingContext(this, this._ctx, this.state);
		this.enterRule(localctx, 74, VelocitronPetriNetParser.RULE_additionalInitialMarking);
		try {
			this.enterOuterAlt(localctx, 1);
			{
			this.state = 330;
			this.initialMarking();
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public namedMarking(): NamedMarkingContext {
		let localctx: NamedMarkingContext = new NamedMarkingContext(this, this._ctx, this.state);
		this.enterRule(localctx, 76, VelocitronPetriNetParser.RULE_namedMarking);
		try {
			this.enterOuterAlt(localctx, 1);
			{
			this.state = 332;
			this.match(VelocitronPetriNetParser.MARKING);
			this.state = 333;
			this.name();
			this.state = 334;
			this.place();
			this.state = 335;
			this.match(VelocitronPetriNetParser.LEFT_ARROW);
			this.state = 336;
			this.markingValue();
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public markingValue(): MarkingValueContext {
		let localctx: MarkingValueContext = new MarkingValueContext(this, this._ctx, this.state);
		this.enterRule(localctx, 78, VelocitronPetriNetParser.RULE_markingValue);
		let _la: number;
		try {
			this.state = 345;
			this._errHandler.sync(this);
			switch ( this._interp.adaptivePredict(this._input, 14, this._ctx) ) {
			case 1:
				this.enterOuterAlt(localctx, 1);
				{
				this.state = 341;
				this._errHandler.sync(this);
				_la = this._input.LA(1);
				if (_la===54) {
					{
					this.state = 338;
					this.positiveInteger();
					this.state = 339;
					this.match(VelocitronPetriNetParser.STAR);
					}
				}

				this.state = 343;
				this.templateReference();
				}
				break;
			case 2:
				this.enterOuterAlt(localctx, 2);
				{
				this.state = 344;
				this.positiveInteger();
				}
				break;
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public metadataDescription(): MetadataDescriptionContext {
		let localctx: MetadataDescriptionContext = new MetadataDescriptionContext(this, this._ctx, this.state);
		this.enterRule(localctx, 80, VelocitronPetriNetParser.RULE_metadataDescription);
		try {
			this.enterOuterAlt(localctx, 1);
			{
			this.state = 347;
			this.metadataTarget();
			this.state = 348;
			this.match(VelocitronPetriNetParser.DESCRIPTION);
			this.state = 349;
			this.match(VelocitronPetriNetParser.STRING);
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public metadataAnnotation(): MetadataAnnotationContext {
		let localctx: MetadataAnnotationContext = new MetadataAnnotationContext(this, this._ctx, this.state);
		this.enterRule(localctx, 82, VelocitronPetriNetParser.RULE_metadataAnnotation);
		try {
			this.enterOuterAlt(localctx, 1);
			{
			this.state = 351;
			this.metadataTarget();
			this.state = 352;
			this.match(VelocitronPetriNetParser.ANNOTATION);
			this.state = 353;
			this.name();
			this.state = 354;
			this.jsonValue();
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public metadataTarget(): MetadataTargetContext {
		let localctx: MetadataTargetContext = new MetadataTargetContext(this, this._ctx, this.state);
		this.enterRule(localctx, 84, VelocitronPetriNetParser.RULE_metadataTarget);
		try {
			this.state = 361;
			this._errHandler.sync(this);
			switch (this._input.LA(1)) {
			case 1:
				this.enterOuterAlt(localctx, 1);
				{
				this.state = 356;
				this.match(VelocitronPetriNetParser.NET);
				}
				break;
			case 38:
				this.enterOuterAlt(localctx, 2);
				{
				this.state = 357;
				this.place();
				}
				break;
			case 40:
				this.enterOuterAlt(localctx, 3);
				{
				this.state = 358;
				this.transition();
				}
				break;
			case 37:
				this.enterOuterAlt(localctx, 4);
				{
				this.state = 359;
				this.match(VelocitronPetriNetParser.AT);
				this.state = 360;
				this.name();
				}
				break;
			default:
				throw new NoViableAltException(this);
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public viewPosition(): ViewPositionContext {
		let localctx: ViewPositionContext = new ViewPositionContext(this, this._ctx, this.state);
		this.enterRule(localctx, 86, VelocitronPetriNetParser.RULE_viewPosition);
		try {
			this.enterOuterAlt(localctx, 1);
			{
			this.state = 363;
			this.match(VelocitronPetriNetParser.VIEW);
			this.state = 364;
			this.name();
			this.state = 365;
			this.match(VelocitronPetriNetParser.POSITION);
			this.state = 366;
			this.viewTarget();
			this.state = 367;
			this.match(VelocitronPetriNetParser.AT_KEYWORD);
			this.state = 368;
			this.jsonObject();
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public viewRoute(): ViewRouteContext {
		let localctx: ViewRouteContext = new ViewRouteContext(this, this._ctx, this.state);
		this.enterRule(localctx, 88, VelocitronPetriNetParser.RULE_viewRoute);
		try {
			this.enterOuterAlt(localctx, 1);
			{
			this.state = 370;
			this.match(VelocitronPetriNetParser.VIEW);
			this.state = 371;
			this.name();
			this.state = 372;
			this.match(VelocitronPetriNetParser.ROUTE);
			this.state = 373;
			this.match(VelocitronPetriNetParser.AT);
			this.state = 374;
			this.name();
			this.state = 375;
			this.match(VelocitronPetriNetParser.ORTHOGONAL);
			this.state = 376;
			this.jsonArray();
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public viewTarget(): ViewTargetContext {
		let localctx: ViewTargetContext = new ViewTargetContext(this, this._ctx, this.state);
		this.enterRule(localctx, 90, VelocitronPetriNetParser.RULE_viewTarget);
		try {
			this.state = 380;
			this._errHandler.sync(this);
			switch (this._input.LA(1)) {
			case 38:
				this.enterOuterAlt(localctx, 1);
				{
				this.state = 378;
				this.place();
				}
				break;
			case 40:
				this.enterOuterAlt(localctx, 2);
				{
				this.state = 379;
				this.transition();
				}
				break;
			default:
				throw new NoViableAltException(this);
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public extensions(): ExtensionsContext {
		let localctx: ExtensionsContext = new ExtensionsContext(this, this._ctx, this.state);
		this.enterRule(localctx, 92, VelocitronPetriNetParser.RULE_extensions);
		try {
			this.enterOuterAlt(localctx, 1);
			{
			this.state = 382;
			this.match(VelocitronPetriNetParser.EXTENSIONS);
			this.state = 383;
			this.jsonObject();
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public templateDefinition(): TemplateDefinitionContext {
		let localctx: TemplateDefinitionContext = new TemplateDefinitionContext(this, this._ctx, this.state);
		this.enterRule(localctx, 94, VelocitronPetriNetParser.RULE_templateDefinition);
		try {
			this.enterOuterAlt(localctx, 1);
			{
			this.state = 385;
			this.templateReference();
			this.state = 386;
			this.match(VelocitronPetriNetParser.COLON);
			this.state = 387;
			this.color();
			this.state = 388;
			this.jsonValue();
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public additionalTemplateDefinition(): AdditionalTemplateDefinitionContext {
		let localctx: AdditionalTemplateDefinitionContext = new AdditionalTemplateDefinitionContext(this, this._ctx, this.state);
		this.enterRule(localctx, 96, VelocitronPetriNetParser.RULE_additionalTemplateDefinition);
		try {
			this.enterOuterAlt(localctx, 1);
			{
			this.state = 390;
			this.templateDefinition();
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public templateReference(): TemplateReferenceContext {
		let localctx: TemplateReferenceContext = new TemplateReferenceContext(this, this._ctx, this.state);
		this.enterRule(localctx, 98, VelocitronPetriNetParser.RULE_templateReference);
		try {
			this.enterOuterAlt(localctx, 1);
			{
			this.state = 392;
			this.match(VelocitronPetriNetParser.DOLLAR);
			this.state = 393;
			this.name();
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public color(): ColorContext {
		let localctx: ColorContext = new ColorContext(this, this._ctx, this.state);
		this.enterRule(localctx, 100, VelocitronPetriNetParser.RULE_color);
		try {
			this.enterOuterAlt(localctx, 1);
			{
			this.state = 395;
			this.name();
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public name(): NameContext {
		let localctx: NameContext = new NameContext(this, this._ctx, this.state);
		this.enterRule(localctx, 102, VelocitronPetriNetParser.RULE_name);
		let _la: number;
		try {
			this.enterOuterAlt(localctx, 1);
			{
			this.state = 397;
			_la = this._input.LA(1);
			if(!((((_la) & ~0x1F) === 0 && ((1 << _la) & 4227953148) !== 0) || ((((_la - 32)) & ~0x1F) === 0 && ((1 << (_la - 32)) & 100663299) !== 0))) {
			this._errHandler.recoverInline(this);
			}
			else {
				this._errHandler.reportMatch(this);
			    this.consume();
			}
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public jsonValue(): JsonValueContext {
		let localctx: JsonValueContext = new JsonValueContext(this, this._ctx, this.state);
		this.enterRule(localctx, 104, VelocitronPetriNetParser.RULE_jsonValue);
		try {
			this.state = 408;
			this._errHandler.sync(this);
			switch (this._input.LA(1)) {
			case 42:
				this.enterOuterAlt(localctx, 1);
				{
				this.state = 399;
				this.jsonObject();
				}
				break;
			case 40:
				this.enterOuterAlt(localctx, 2);
				{
				this.state = 400;
				this.jsonArray();
				}
				break;
			case 57:
				this.enterOuterAlt(localctx, 3);
				{
				this.state = 401;
				this.match(VelocitronPetriNetParser.STRING);
				}
				break;
			case 56:
				this.enterOuterAlt(localctx, 4);
				{
				this.state = 402;
				this.match(VelocitronPetriNetParser.NUMBER);
				}
				break;
			case 54:
				this.enterOuterAlt(localctx, 5);
				{
				this.state = 403;
				this.match(VelocitronPetriNetParser.POSITIVE_INTEGER);
				}
				break;
			case 55:
				this.enterOuterAlt(localctx, 6);
				{
				this.state = 404;
				this.match(VelocitronPetriNetParser.ZERO);
				}
				break;
			case 34:
				this.enterOuterAlt(localctx, 7);
				{
				this.state = 405;
				this.match(VelocitronPetriNetParser.TRUE);
				}
				break;
			case 35:
				this.enterOuterAlt(localctx, 8);
				{
				this.state = 406;
				this.match(VelocitronPetriNetParser.FALSE);
				}
				break;
			case 36:
				this.enterOuterAlt(localctx, 9);
				{
				this.state = 407;
				this.match(VelocitronPetriNetParser.NULL);
				}
				break;
			default:
				throw new NoViableAltException(this);
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public jsonObject(): JsonObjectContext {
		let localctx: JsonObjectContext = new JsonObjectContext(this, this._ctx, this.state);
		this.enterRule(localctx, 106, VelocitronPetriNetParser.RULE_jsonObject);
		let _la: number;
		try {
			this.enterOuterAlt(localctx, 1);
			{
			this.state = 410;
			this.match(VelocitronPetriNetParser.LBRACE);
			this.state = 423;
			this._errHandler.sync(this);
			_la = this._input.LA(1);
			if (_la===57) {
				{
				this.state = 411;
				this.match(VelocitronPetriNetParser.STRING);
				this.state = 412;
				this.match(VelocitronPetriNetParser.COLON);
				this.state = 413;
				this.jsonValue();
				this.state = 420;
				this._errHandler.sync(this);
				_la = this._input.LA(1);
				while (_la===52) {
					{
					{
					this.state = 414;
					this.match(VelocitronPetriNetParser.COMMA);
					this.state = 415;
					this.match(VelocitronPetriNetParser.STRING);
					this.state = 416;
					this.match(VelocitronPetriNetParser.COLON);
					this.state = 417;
					this.jsonValue();
					}
					}
					this.state = 422;
					this._errHandler.sync(this);
					_la = this._input.LA(1);
				}
				}
			}

			this.state = 425;
			this.match(VelocitronPetriNetParser.RBRACE);
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}
	// @RuleVersion(0)
	public jsonArray(): JsonArrayContext {
		let localctx: JsonArrayContext = new JsonArrayContext(this, this._ctx, this.state);
		this.enterRule(localctx, 108, VelocitronPetriNetParser.RULE_jsonArray);
		let _la: number;
		try {
			this.enterOuterAlt(localctx, 1);
			{
			this.state = 427;
			this.match(VelocitronPetriNetParser.LBRACK);
			this.state = 436;
			this._errHandler.sync(this);
			_la = this._input.LA(1);
			if (((((_la - 34)) & ~0x1F) === 0 && ((1 << (_la - 34)) & 15728967) !== 0)) {
				{
				this.state = 428;
				this.jsonValue();
				this.state = 433;
				this._errHandler.sync(this);
				_la = this._input.LA(1);
				while (_la===52) {
					{
					{
					this.state = 429;
					this.match(VelocitronPetriNetParser.COMMA);
					this.state = 430;
					this.jsonValue();
					}
					}
					this.state = 435;
					this._errHandler.sync(this);
					_la = this._input.LA(1);
				}
				}
			}

			this.state = 438;
			this.match(VelocitronPetriNetParser.RBRACK);
			}
		}
		catch (re) {
			if (re instanceof RecognitionException) {
				localctx.exception = re;
				this._errHandler.reportError(this, re);
				this._errHandler.recover(this, re);
			} else {
				throw re;
			}
		}
		finally {
			this.exitRule();
		}
		return localctx;
	}

	public static readonly _serializedATN: number[] = [4,1,63,441,2,0,7,0,2,
	1,7,1,2,2,7,2,2,3,7,3,2,4,7,4,2,5,7,5,2,6,7,6,2,7,7,7,2,8,7,8,2,9,7,9,2,
	10,7,10,2,11,7,11,2,12,7,12,2,13,7,13,2,14,7,14,2,15,7,15,2,16,7,16,2,17,
	7,17,2,18,7,18,2,19,7,19,2,20,7,20,2,21,7,21,2,22,7,22,2,23,7,23,2,24,7,
	24,2,25,7,25,2,26,7,26,2,27,7,27,2,28,7,28,2,29,7,29,2,30,7,30,2,31,7,31,
	2,32,7,32,2,33,7,33,2,34,7,34,2,35,7,35,2,36,7,36,2,37,7,37,2,38,7,38,2,
	39,7,39,2,40,7,40,2,41,7,41,2,42,7,42,2,43,7,43,2,44,7,44,2,45,7,45,2,46,
	7,46,2,47,7,47,2,48,7,48,2,49,7,49,2,50,7,50,2,51,7,51,2,52,7,52,2,53,7,
	53,2,54,7,54,1,0,3,0,112,8,0,1,0,1,0,1,0,1,0,1,0,1,0,1,0,1,0,1,0,1,0,1,
	0,1,0,1,0,1,0,1,0,1,0,1,0,1,0,1,0,1,0,1,0,1,0,1,0,1,0,1,0,1,0,5,0,140,8,
	0,10,0,12,0,143,9,0,1,0,1,0,1,0,1,0,5,0,149,8,0,10,0,12,0,152,9,0,1,0,1,
	0,3,0,156,8,0,1,1,1,1,1,1,3,1,161,8,1,1,2,1,2,1,2,1,3,1,3,1,3,1,3,1,3,1,
	4,1,4,1,4,1,4,1,4,1,4,1,4,1,4,1,4,1,5,3,5,181,8,5,1,5,1,5,1,5,1,5,4,5,187,
	8,5,11,5,12,5,188,1,6,1,6,3,6,193,8,6,1,7,1,7,3,7,197,8,7,1,7,1,7,1,8,1,
	8,1,9,1,9,1,10,1,10,1,10,1,10,1,11,1,11,1,11,1,11,1,12,1,12,1,12,1,12,1,
	13,1,13,1,14,1,14,1,15,1,15,1,15,1,15,1,15,1,16,1,16,1,17,1,17,1,17,1,17,
	1,18,1,18,1,19,1,19,1,19,1,19,1,20,1,20,1,20,1,20,1,20,1,20,1,20,1,21,1,
	21,1,21,1,21,1,21,1,21,1,22,1,22,1,22,1,22,1,22,1,22,1,23,1,23,1,24,1,24,
	1,24,1,24,1,25,1,25,1,25,1,25,1,26,1,26,1,26,1,26,1,26,1,27,1,27,1,27,1,
	27,1,27,1,27,5,27,278,8,27,10,27,12,27,281,9,27,1,27,1,27,1,28,1,28,1,28,
	1,28,1,29,1,29,1,29,1,29,1,29,1,30,1,30,1,30,1,30,1,30,1,30,1,30,1,30,1,
	30,1,30,1,30,3,30,305,8,30,1,31,1,31,1,31,1,31,1,31,1,31,1,32,1,32,1,32,
	1,32,1,32,1,32,1,33,1,33,1,34,1,34,1,35,1,35,1,36,1,36,1,36,1,36,1,36,1,
	36,1,37,1,37,1,38,1,38,1,38,1,38,1,38,1,38,1,39,1,39,1,39,3,39,342,8,39,
	1,39,1,39,3,39,346,8,39,1,40,1,40,1,40,1,40,1,41,1,41,1,41,1,41,1,41,1,
	42,1,42,1,42,1,42,1,42,3,42,362,8,42,1,43,1,43,1,43,1,43,1,43,1,43,1,43,
	1,44,1,44,1,44,1,44,1,44,1,44,1,44,1,44,1,45,1,45,3,45,381,8,45,1,46,1,
	46,1,46,1,47,1,47,1,47,1,47,1,47,1,48,1,48,1,49,1,49,1,49,1,50,1,50,1,51,
	1,51,1,52,1,52,1,52,1,52,1,52,1,52,1,52,1,52,1,52,3,52,409,8,52,1,53,1,
	53,1,53,1,53,1,53,1,53,1,53,1,53,5,53,419,8,53,10,53,12,53,422,9,53,3,53,
	424,8,53,1,53,1,53,1,54,1,54,1,54,1,54,5,54,432,8,54,10,54,12,54,435,9,
	54,3,54,437,8,54,1,54,1,54,1,54,0,0,55,0,2,4,6,8,10,12,14,16,18,20,22,24,
	26,28,30,32,34,36,38,40,42,44,46,48,50,52,54,56,58,60,62,64,66,68,70,72,
	74,76,78,80,82,84,86,88,90,92,94,96,98,100,102,104,106,108,0,6,1,0,45,47,
	1,0,7,8,2,0,13,13,58,58,2,0,9,9,22,22,1,0,54,55,5,0,2,8,12,14,16,16,26,
	33,57,58,440,0,155,1,0,0,0,2,157,1,0,0,0,4,162,1,0,0,0,6,165,1,0,0,0,8,
	170,1,0,0,0,10,180,1,0,0,0,12,192,1,0,0,0,14,196,1,0,0,0,16,200,1,0,0,0,
	18,202,1,0,0,0,20,204,1,0,0,0,22,208,1,0,0,0,24,212,1,0,0,0,26,216,1,0,
	0,0,28,218,1,0,0,0,30,220,1,0,0,0,32,225,1,0,0,0,34,227,1,0,0,0,36,231,
	1,0,0,0,38,233,1,0,0,0,40,237,1,0,0,0,42,244,1,0,0,0,44,250,1,0,0,0,46,
	256,1,0,0,0,48,258,1,0,0,0,50,262,1,0,0,0,52,266,1,0,0,0,54,271,1,0,0,0,
	56,284,1,0,0,0,58,288,1,0,0,0,60,304,1,0,0,0,62,306,1,0,0,0,64,312,1,0,
	0,0,66,318,1,0,0,0,68,320,1,0,0,0,70,322,1,0,0,0,72,324,1,0,0,0,74,330,
	1,0,0,0,76,332,1,0,0,0,78,345,1,0,0,0,80,347,1,0,0,0,82,351,1,0,0,0,84,
	361,1,0,0,0,86,363,1,0,0,0,88,370,1,0,0,0,90,380,1,0,0,0,92,382,1,0,0,0,
	94,385,1,0,0,0,96,390,1,0,0,0,98,392,1,0,0,0,100,395,1,0,0,0,102,397,1,
	0,0,0,104,408,1,0,0,0,106,410,1,0,0,0,108,427,1,0,0,0,110,112,3,2,1,0,111,
	110,1,0,0,0,111,112,1,0,0,0,112,141,1,0,0,0,113,140,3,10,5,0,114,140,3,
	26,13,0,115,140,3,28,14,0,116,140,3,34,17,0,117,140,3,38,19,0,118,140,3,
	40,20,0,119,140,3,42,21,0,120,140,3,44,22,0,121,140,3,48,24,0,122,140,3,
	50,25,0,123,140,3,52,26,0,124,140,3,30,15,0,125,140,3,54,27,0,126,140,3,
	56,28,0,127,140,3,58,29,0,128,140,3,60,30,0,129,140,3,62,31,0,130,140,3,
	64,32,0,131,140,3,72,36,0,132,140,3,94,47,0,133,140,3,76,38,0,134,140,3,
	80,40,0,135,140,3,82,41,0,136,140,3,86,43,0,137,140,3,88,44,0,138,140,3,
	92,46,0,139,113,1,0,0,0,139,114,1,0,0,0,139,115,1,0,0,0,139,116,1,0,0,0,
	139,117,1,0,0,0,139,118,1,0,0,0,139,119,1,0,0,0,139,120,1,0,0,0,139,121,
	1,0,0,0,139,122,1,0,0,0,139,123,1,0,0,0,139,124,1,0,0,0,139,125,1,0,0,0,
	139,126,1,0,0,0,139,127,1,0,0,0,139,128,1,0,0,0,139,129,1,0,0,0,139,130,
	1,0,0,0,139,131,1,0,0,0,139,132,1,0,0,0,139,133,1,0,0,0,139,134,1,0,0,0,
	139,135,1,0,0,0,139,136,1,0,0,0,139,137,1,0,0,0,139,138,1,0,0,0,140,143,
	1,0,0,0,141,139,1,0,0,0,141,142,1,0,0,0,142,144,1,0,0,0,143,141,1,0,0,0,
	144,156,5,0,0,1,145,150,3,4,2,0,146,149,3,6,3,0,147,149,3,8,4,0,148,146,
	1,0,0,0,148,147,1,0,0,0,149,152,1,0,0,0,150,148,1,0,0,0,150,151,1,0,0,0,
	151,153,1,0,0,0,152,150,1,0,0,0,153,154,5,0,0,1,154,156,1,0,0,0,155,111,
	1,0,0,0,155,145,1,0,0,0,156,1,1,0,0,0,157,158,5,1,0,0,158,160,3,102,51,
	0,159,161,5,57,0,0,160,159,1,0,0,0,160,161,1,0,0,0,161,3,1,0,0,0,162,163,
	5,2,0,0,163,164,3,102,51,0,164,5,1,0,0,0,165,166,5,3,0,0,166,167,5,57,0,
	0,167,168,5,4,0,0,168,169,5,58,0,0,169,7,1,0,0,0,170,171,5,5,0,0,171,172,
	5,58,0,0,172,173,5,53,0,0,173,174,3,22,11,0,174,175,5,47,0,0,175,176,5,
	58,0,0,176,177,5,53,0,0,177,178,3,22,11,0,178,9,1,0,0,0,179,181,3,20,10,
	0,180,179,1,0,0,0,180,181,1,0,0,0,181,182,1,0,0,0,182,186,3,12,6,0,183,
	184,3,14,7,0,184,185,3,12,6,0,185,187,1,0,0,0,186,183,1,0,0,0,187,188,1,
	0,0,0,188,186,1,0,0,0,188,189,1,0,0,0,189,11,1,0,0,0,190,193,3,22,11,0,
	191,193,3,24,12,0,192,190,1,0,0,0,192,191,1,0,0,0,193,13,1,0,0,0,194,195,
	5,44,0,0,195,197,3,100,50,0,196,194,1,0,0,0,196,197,1,0,0,0,197,198,1,0,
	0,0,198,199,3,16,8,0,199,15,1,0,0,0,200,201,7,0,0,0,201,17,1,0,0,0,202,
	203,3,10,5,0,203,19,1,0,0,0,204,205,5,37,0,0,205,206,3,102,51,0,206,207,
	5,51,0,0,207,21,1,0,0,0,208,209,5,38,0,0,209,210,3,102,51,0,210,211,5,39,
	0,0,211,23,1,0,0,0,212,213,5,40,0,0,213,214,3,102,51,0,214,215,5,41,0,0,
	215,25,1,0,0,0,216,217,3,22,11,0,217,27,1,0,0,0,218,219,3,24,12,0,219,29,
	1,0,0,0,220,221,3,22,11,0,221,222,5,6,0,0,222,223,3,32,16,0,223,224,3,100,
	50,0,224,31,1,0,0,0,225,226,7,1,0,0,226,33,1,0,0,0,227,228,3,24,12,0,228,
	229,5,9,0,0,229,230,5,57,0,0,230,35,1,0,0,0,231,232,3,34,17,0,232,37,1,
	0,0,0,233,234,3,24,12,0,234,235,5,10,0,0,235,236,5,57,0,0,236,39,1,0,0,
	0,237,238,3,24,12,0,238,239,5,12,0,0,239,240,5,13,0,0,240,241,3,22,11,0,
	241,242,5,22,0,0,242,243,5,57,0,0,243,41,1,0,0,0,244,245,3,24,12,0,245,
	246,5,12,0,0,246,247,5,14,0,0,247,248,3,46,23,0,248,249,3,22,11,0,249,43,
	1,0,0,0,250,251,3,24,12,0,251,252,5,12,0,0,252,253,5,15,0,0,253,254,5,22,
	0,0,254,255,5,57,0,0,255,45,1,0,0,0,256,257,7,2,0,0,257,47,1,0,0,0,258,
	259,3,24,12,0,259,260,5,16,0,0,260,261,3,70,35,0,261,49,1,0,0,0,262,263,
	3,24,12,0,263,264,5,11,0,0,264,265,3,68,34,0,265,51,1,0,0,0,266,267,5,37,
	0,0,267,268,3,102,51,0,268,269,5,11,0,0,269,270,3,68,34,0,270,53,1,0,0,
	0,271,272,3,22,11,0,272,273,5,17,0,0,273,274,5,40,0,0,274,279,3,100,50,
	0,275,276,5,52,0,0,276,278,3,100,50,0,277,275,1,0,0,0,278,281,1,0,0,0,279,
	277,1,0,0,0,279,280,1,0,0,0,280,282,1,0,0,0,281,279,1,0,0,0,282,283,5,41,
	0,0,283,55,1,0,0,0,284,285,3,22,11,0,285,286,5,18,0,0,286,287,3,106,53,
	0,287,57,1,0,0,0,288,289,5,37,0,0,289,290,3,102,51,0,290,291,5,19,0,0,291,
	292,3,104,52,0,292,59,1,0,0,0,293,294,5,37,0,0,294,295,3,102,51,0,295,296,
	5,20,0,0,296,297,5,22,0,0,297,298,5,57,0,0,298,305,1,0,0,0,299,300,5,37,
	0,0,300,301,3,102,51,0,301,302,5,20,0,0,302,303,3,104,52,0,303,305,1,0,
	0,0,304,293,1,0,0,0,304,299,1,0,0,0,305,61,1,0,0,0,306,307,5,37,0,0,307,
	308,3,102,51,0,308,309,5,21,0,0,309,310,3,66,33,0,310,311,5,57,0,0,311,
	63,1,0,0,0,312,313,5,37,0,0,313,314,3,102,51,0,314,315,5,23,0,0,315,316,
	5,22,0,0,316,317,5,57,0,0,317,65,1,0,0,0,318,319,7,3,0,0,319,67,1,0,0,0,
	320,321,5,54,0,0,321,69,1,0,0,0,322,323,7,4,0,0,323,71,1,0,0,0,324,325,
	5,24,0,0,325,326,5,25,0,0,326,327,3,22,11,0,327,328,5,48,0,0,328,329,3,
	78,39,0,329,73,1,0,0,0,330,331,3,72,36,0,331,75,1,0,0,0,332,333,5,24,0,
	0,333,334,3,102,51,0,334,335,3,22,11,0,335,336,5,48,0,0,336,337,3,78,39,
	0,337,77,1,0,0,0,338,339,3,68,34,0,339,340,5,50,0,0,340,342,1,0,0,0,341,
	338,1,0,0,0,341,342,1,0,0,0,342,343,1,0,0,0,343,346,3,98,49,0,344,346,3,
	68,34,0,345,341,1,0,0,0,345,344,1,0,0,0,346,79,1,0,0,0,347,348,3,84,42,
	0,348,349,5,26,0,0,349,350,5,57,0,0,350,81,1,0,0,0,351,352,3,84,42,0,352,
	353,5,27,0,0,353,354,3,102,51,0,354,355,3,104,52,0,355,83,1,0,0,0,356,362,
	5,1,0,0,357,362,3,22,11,0,358,362,3,24,12,0,359,360,5,37,0,0,360,362,3,
	102,51,0,361,356,1,0,0,0,361,357,1,0,0,0,361,358,1,0,0,0,361,359,1,0,0,
	0,362,85,1,0,0,0,363,364,5,29,0,0,364,365,3,102,51,0,365,366,5,30,0,0,366,
	367,3,90,45,0,367,368,5,32,0,0,368,369,3,106,53,0,369,87,1,0,0,0,370,371,
	5,29,0,0,371,372,3,102,51,0,372,373,5,31,0,0,373,374,5,37,0,0,374,375,3,
	102,51,0,375,376,5,33,0,0,376,377,3,108,54,0,377,89,1,0,0,0,378,381,3,22,
	11,0,379,381,3,24,12,0,380,378,1,0,0,0,380,379,1,0,0,0,381,91,1,0,0,0,382,
	383,5,28,0,0,383,384,3,106,53,0,384,93,1,0,0,0,385,386,3,98,49,0,386,387,
	5,51,0,0,387,388,3,100,50,0,388,389,3,104,52,0,389,95,1,0,0,0,390,391,3,
	94,47,0,391,97,1,0,0,0,392,393,5,49,0,0,393,394,3,102,51,0,394,99,1,0,0,
	0,395,396,3,102,51,0,396,101,1,0,0,0,397,398,7,5,0,0,398,103,1,0,0,0,399,
	409,3,106,53,0,400,409,3,108,54,0,401,409,5,57,0,0,402,409,5,56,0,0,403,
	409,5,54,0,0,404,409,5,55,0,0,405,409,5,34,0,0,406,409,5,35,0,0,407,409,
	5,36,0,0,408,399,1,0,0,0,408,400,1,0,0,0,408,401,1,0,0,0,408,402,1,0,0,
	0,408,403,1,0,0,0,408,404,1,0,0,0,408,405,1,0,0,0,408,406,1,0,0,0,408,407,
	1,0,0,0,409,105,1,0,0,0,410,423,5,42,0,0,411,412,5,57,0,0,412,413,5,51,
	0,0,413,420,3,104,52,0,414,415,5,52,0,0,415,416,5,57,0,0,416,417,5,51,0,
	0,417,419,3,104,52,0,418,414,1,0,0,0,419,422,1,0,0,0,420,418,1,0,0,0,420,
	421,1,0,0,0,421,424,1,0,0,0,422,420,1,0,0,0,423,411,1,0,0,0,423,424,1,0,
	0,0,424,425,1,0,0,0,425,426,5,43,0,0,426,107,1,0,0,0,427,436,5,40,0,0,428,
	433,3,104,52,0,429,430,5,52,0,0,430,432,3,104,52,0,431,429,1,0,0,0,432,
	435,1,0,0,0,433,431,1,0,0,0,433,434,1,0,0,0,434,437,1,0,0,0,435,433,1,0,
	0,0,436,428,1,0,0,0,436,437,1,0,0,0,437,438,1,0,0,0,438,439,5,41,0,0,439,
	109,1,0,0,0,22,111,139,141,148,150,155,160,180,188,192,196,279,304,341,
	345,361,380,408,420,423,433,436];

	private static __ATN: ATN;
	public static get _ATN(): ATN {
		if (!VelocitronPetriNetParser.__ATN) {
			VelocitronPetriNetParser.__ATN = new ATNDeserializer().deserialize(VelocitronPetriNetParser._serializedATN);
		}

		return VelocitronPetriNetParser.__ATN;
	}


	static DecisionsToDFA = VelocitronPetriNetParser._ATN.decisionToState.map( (ds: DecisionState, index: number) => new DFA(ds, index) );

}

export class DocumentContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public EOF(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.EOF, 0);
	}
	public netHeader(): NetHeaderContext {
		return this.getTypedRuleContext(NetHeaderContext, 0) as NetHeaderContext;
	}
	public chain_list(): ChainContext[] {
		return this.getTypedRuleContexts(ChainContext) as ChainContext[];
	}
	public chain(i: number): ChainContext {
		return this.getTypedRuleContext(ChainContext, i) as ChainContext;
	}
	public placeDeclaration_list(): PlaceDeclarationContext[] {
		return this.getTypedRuleContexts(PlaceDeclarationContext) as PlaceDeclarationContext[];
	}
	public placeDeclaration(i: number): PlaceDeclarationContext {
		return this.getTypedRuleContext(PlaceDeclarationContext, i) as PlaceDeclarationContext;
	}
	public transitionDeclaration_list(): TransitionDeclarationContext[] {
		return this.getTypedRuleContexts(TransitionDeclarationContext) as TransitionDeclarationContext[];
	}
	public transitionDeclaration(i: number): TransitionDeclarationContext {
		return this.getTypedRuleContext(TransitionDeclarationContext, i) as TransitionDeclarationContext;
	}
	public transitionHandler_list(): TransitionHandlerContext[] {
		return this.getTypedRuleContexts(TransitionHandlerContext) as TransitionHandlerContext[];
	}
	public transitionHandler(i: number): TransitionHandlerContext {
		return this.getTypedRuleContext(TransitionHandlerContext, i) as TransitionHandlerContext;
	}
	public transitionGuard_list(): TransitionGuardContext[] {
		return this.getTypedRuleContexts(TransitionGuardContext) as TransitionGuardContext[];
	}
	public transitionGuard(i: number): TransitionGuardContext {
		return this.getTypedRuleContext(TransitionGuardContext, i) as TransitionGuardContext;
	}
	public transitionTimer_list(): TransitionTimerContext[] {
		return this.getTypedRuleContexts(TransitionTimerContext) as TransitionTimerContext[];
	}
	public transitionTimer(i: number): TransitionTimerContext {
		return this.getTypedRuleContext(TransitionTimerContext, i) as TransitionTimerContext;
	}
	public transitionTimerBind_list(): TransitionTimerBindContext[] {
		return this.getTypedRuleContexts(TransitionTimerBindContext) as TransitionTimerBindContext[];
	}
	public transitionTimerBind(i: number): TransitionTimerBindContext {
		return this.getTypedRuleContext(TransitionTimerBindContext, i) as TransitionTimerBindContext;
	}
	public transitionTimerMaturity_list(): TransitionTimerMaturityContext[] {
		return this.getTypedRuleContexts(TransitionTimerMaturityContext) as TransitionTimerMaturityContext[];
	}
	public transitionTimerMaturity(i: number): TransitionTimerMaturityContext {
		return this.getTypedRuleContext(TransitionTimerMaturityContext, i) as TransitionTimerMaturityContext;
	}
	public transitionPriority_list(): TransitionPriorityContext[] {
		return this.getTypedRuleContexts(TransitionPriorityContext) as TransitionPriorityContext[];
	}
	public transitionPriority(i: number): TransitionPriorityContext {
		return this.getTypedRuleContext(TransitionPriorityContext, i) as TransitionPriorityContext;
	}
	public transitionOrder_list(): TransitionOrderContext[] {
		return this.getTypedRuleContexts(TransitionOrderContext) as TransitionOrderContext[];
	}
	public transitionOrder(i: number): TransitionOrderContext {
		return this.getTypedRuleContext(TransitionOrderContext, i) as TransitionOrderContext;
	}
	public chainOrder_list(): ChainOrderContext[] {
		return this.getTypedRuleContexts(ChainOrderContext) as ChainOrderContext[];
	}
	public chainOrder(i: number): ChainOrderContext {
		return this.getTypedRuleContext(ChainOrderContext, i) as ChainOrderContext;
	}
	public placePort_list(): PlacePortContext[] {
		return this.getTypedRuleContexts(PlacePortContext) as PlacePortContext[];
	}
	public placePort(i: number): PlacePortContext {
		return this.getTypedRuleContext(PlacePortContext, i) as PlacePortContext;
	}
	public placeAccepts_list(): PlaceAcceptsContext[] {
		return this.getTypedRuleContexts(PlaceAcceptsContext) as PlaceAcceptsContext[];
	}
	public placeAccepts(i: number): PlaceAcceptsContext {
		return this.getTypedRuleContext(PlaceAcceptsContext, i) as PlaceAcceptsContext;
	}
	public placeCapacity_list(): PlaceCapacityContext[] {
		return this.getTypedRuleContexts(PlaceCapacityContext) as PlaceCapacityContext[];
	}
	public placeCapacity(i: number): PlaceCapacityContext {
		return this.getTypedRuleContext(PlaceCapacityContext, i) as PlaceCapacityContext;
	}
	public arcWeight_list(): ArcWeightContext[] {
		return this.getTypedRuleContexts(ArcWeightContext) as ArcWeightContext[];
	}
	public arcWeight(i: number): ArcWeightContext {
		return this.getTypedRuleContext(ArcWeightContext, i) as ArcWeightContext;
	}
	public arcData_list(): ArcDataContext[] {
		return this.getTypedRuleContexts(ArcDataContext) as ArcDataContext[];
	}
	public arcData(i: number): ArcDataContext {
		return this.getTypedRuleContext(ArcDataContext, i) as ArcDataContext;
	}
	public arcPredicate_list(): ArcPredicateContext[] {
		return this.getTypedRuleContexts(ArcPredicateContext) as ArcPredicateContext[];
	}
	public arcPredicate(i: number): ArcPredicateContext {
		return this.getTypedRuleContext(ArcPredicateContext, i) as ArcPredicateContext;
	}
	public arcCorrelate_list(): ArcCorrelateContext[] {
		return this.getTypedRuleContexts(ArcCorrelateContext) as ArcCorrelateContext[];
	}
	public arcCorrelate(i: number): ArcCorrelateContext {
		return this.getTypedRuleContext(ArcCorrelateContext, i) as ArcCorrelateContext;
	}
	public initialMarking_list(): InitialMarkingContext[] {
		return this.getTypedRuleContexts(InitialMarkingContext) as InitialMarkingContext[];
	}
	public initialMarking(i: number): InitialMarkingContext {
		return this.getTypedRuleContext(InitialMarkingContext, i) as InitialMarkingContext;
	}
	public templateDefinition_list(): TemplateDefinitionContext[] {
		return this.getTypedRuleContexts(TemplateDefinitionContext) as TemplateDefinitionContext[];
	}
	public templateDefinition(i: number): TemplateDefinitionContext {
		return this.getTypedRuleContext(TemplateDefinitionContext, i) as TemplateDefinitionContext;
	}
	public namedMarking_list(): NamedMarkingContext[] {
		return this.getTypedRuleContexts(NamedMarkingContext) as NamedMarkingContext[];
	}
	public namedMarking(i: number): NamedMarkingContext {
		return this.getTypedRuleContext(NamedMarkingContext, i) as NamedMarkingContext;
	}
	public metadataDescription_list(): MetadataDescriptionContext[] {
		return this.getTypedRuleContexts(MetadataDescriptionContext) as MetadataDescriptionContext[];
	}
	public metadataDescription(i: number): MetadataDescriptionContext {
		return this.getTypedRuleContext(MetadataDescriptionContext, i) as MetadataDescriptionContext;
	}
	public metadataAnnotation_list(): MetadataAnnotationContext[] {
		return this.getTypedRuleContexts(MetadataAnnotationContext) as MetadataAnnotationContext[];
	}
	public metadataAnnotation(i: number): MetadataAnnotationContext {
		return this.getTypedRuleContext(MetadataAnnotationContext, i) as MetadataAnnotationContext;
	}
	public viewPosition_list(): ViewPositionContext[] {
		return this.getTypedRuleContexts(ViewPositionContext) as ViewPositionContext[];
	}
	public viewPosition(i: number): ViewPositionContext {
		return this.getTypedRuleContext(ViewPositionContext, i) as ViewPositionContext;
	}
	public viewRoute_list(): ViewRouteContext[] {
		return this.getTypedRuleContexts(ViewRouteContext) as ViewRouteContext[];
	}
	public viewRoute(i: number): ViewRouteContext {
		return this.getTypedRuleContext(ViewRouteContext, i) as ViewRouteContext;
	}
	public extensions_list(): ExtensionsContext[] {
		return this.getTypedRuleContexts(ExtensionsContext) as ExtensionsContext[];
	}
	public extensions(i: number): ExtensionsContext {
		return this.getTypedRuleContext(ExtensionsContext, i) as ExtensionsContext;
	}
	public compositionHeader(): CompositionHeaderContext {
		return this.getTypedRuleContext(CompositionHeaderContext, 0) as CompositionHeaderContext;
	}
	public compositionUse_list(): CompositionUseContext[] {
		return this.getTypedRuleContexts(CompositionUseContext) as CompositionUseContext[];
	}
	public compositionUse(i: number): CompositionUseContext {
		return this.getTypedRuleContext(CompositionUseContext, i) as CompositionUseContext;
	}
	public compositionWire_list(): CompositionWireContext[] {
		return this.getTypedRuleContexts(CompositionWireContext) as CompositionWireContext[];
	}
	public compositionWire(i: number): CompositionWireContext {
		return this.getTypedRuleContext(CompositionWireContext, i) as CompositionWireContext;
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_document;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitDocument) {
			return visitor.visitDocument(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class NetHeaderContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public NET(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.NET, 0);
	}
	public name(): NameContext {
		return this.getTypedRuleContext(NameContext, 0) as NameContext;
	}
	public STRING(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.STRING, 0);
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_netHeader;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitNetHeader) {
			return visitor.visitNetHeader(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class CompositionHeaderContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public COMPOSITION(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.COMPOSITION, 0);
	}
	public name(): NameContext {
		return this.getTypedRuleContext(NameContext, 0) as NameContext;
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_compositionHeader;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitCompositionHeader) {
			return visitor.visitCompositionHeader(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class CompositionUseContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public USE(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.USE, 0);
	}
	public STRING(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.STRING, 0);
	}
	public AS(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.AS, 0);
	}
	public IDENT(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.IDENT, 0);
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_compositionUse;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitCompositionUse) {
			return visitor.visitCompositionUse(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class CompositionWireContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public WIRE(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.WIRE, 0);
	}
	public IDENT_list(): TerminalNode[] {
	    	return this.getTokens(VelocitronPetriNetParser.IDENT);
	}
	public IDENT(i: number): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.IDENT, i);
	}
	public DOT_list(): TerminalNode[] {
	    	return this.getTokens(VelocitronPetriNetParser.DOT);
	}
	public DOT(i: number): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.DOT, i);
	}
	public place_list(): PlaceContext[] {
		return this.getTypedRuleContexts(PlaceContext) as PlaceContext[];
	}
	public place(i: number): PlaceContext {
		return this.getTypedRuleContext(PlaceContext, i) as PlaceContext;
	}
	public ARROW(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.ARROW, 0);
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_compositionWire;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitCompositionWire) {
			return visitor.visitCompositionWire(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class ChainContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public chainNode_list(): ChainNodeContext[] {
		return this.getTypedRuleContexts(ChainNodeContext) as ChainNodeContext[];
	}
	public chainNode(i: number): ChainNodeContext {
		return this.getTypedRuleContext(ChainNodeContext, i) as ChainNodeContext;
	}
	public chainHandle(): ChainHandleContext {
		return this.getTypedRuleContext(ChainHandleContext, 0) as ChainHandleContext;
	}
	public arcSegment_list(): ArcSegmentContext[] {
		return this.getTypedRuleContexts(ArcSegmentContext) as ArcSegmentContext[];
	}
	public arcSegment(i: number): ArcSegmentContext {
		return this.getTypedRuleContext(ArcSegmentContext, i) as ArcSegmentContext;
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_chain;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitChain) {
			return visitor.visitChain(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class ChainNodeContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public place(): PlaceContext {
		return this.getTypedRuleContext(PlaceContext, 0) as PlaceContext;
	}
	public transition(): TransitionContext {
		return this.getTypedRuleContext(TransitionContext, 0) as TransitionContext;
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_chainNode;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitChainNode) {
			return visitor.visitChainNode(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class ArcSegmentContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public arcOperator(): ArcOperatorContext {
		return this.getTypedRuleContext(ArcOperatorContext, 0) as ArcOperatorContext;
	}
	public HYPHEN(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.HYPHEN, 0);
	}
	public color(): ColorContext {
		return this.getTypedRuleContext(ColorContext, 0) as ColorContext;
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_arcSegment;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitArcSegment) {
			return visitor.visitArcSegment(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class ArcOperatorContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public ARROW(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.ARROW, 0);
	}
	public READ_ARROW(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.READ_ARROW, 0);
	}
	public INHIBIT_ARROW(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.INHIBIT_ARROW, 0);
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_arcOperator;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitArcOperator) {
			return visitor.visitArcOperator(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class AdditionalChainContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public chain(): ChainContext {
		return this.getTypedRuleContext(ChainContext, 0) as ChainContext;
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_additionalChain;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitAdditionalChain) {
			return visitor.visitAdditionalChain(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class ChainHandleContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public AT(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.AT, 0);
	}
	public name(): NameContext {
		return this.getTypedRuleContext(NameContext, 0) as NameContext;
	}
	public COLON(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.COLON, 0);
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_chainHandle;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitChainHandle) {
			return visitor.visitChainHandle(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class PlaceContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public LPAREN(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.LPAREN, 0);
	}
	public name(): NameContext {
		return this.getTypedRuleContext(NameContext, 0) as NameContext;
	}
	public RPAREN(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.RPAREN, 0);
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_place;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitPlace) {
			return visitor.visitPlace(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class TransitionContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public LBRACK(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.LBRACK, 0);
	}
	public name(): NameContext {
		return this.getTypedRuleContext(NameContext, 0) as NameContext;
	}
	public RBRACK(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.RBRACK, 0);
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_transition;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitTransition) {
			return visitor.visitTransition(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class PlaceDeclarationContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public place(): PlaceContext {
		return this.getTypedRuleContext(PlaceContext, 0) as PlaceContext;
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_placeDeclaration;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitPlaceDeclaration) {
			return visitor.visitPlaceDeclaration(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class TransitionDeclarationContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public transition(): TransitionContext {
		return this.getTypedRuleContext(TransitionContext, 0) as TransitionContext;
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_transitionDeclaration;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitTransitionDeclaration) {
			return visitor.visitTransitionDeclaration(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class PlacePortContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public place(): PlaceContext {
		return this.getTypedRuleContext(PlaceContext, 0) as PlaceContext;
	}
	public PORT(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.PORT, 0);
	}
	public portDirection(): PortDirectionContext {
		return this.getTypedRuleContext(PortDirectionContext, 0) as PortDirectionContext;
	}
	public color(): ColorContext {
		return this.getTypedRuleContext(ColorContext, 0) as ColorContext;
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_placePort;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitPlacePort) {
			return visitor.visitPlacePort(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class PortDirectionContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public INPUT(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.INPUT, 0);
	}
	public OUTPUT(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.OUTPUT, 0);
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_portDirection;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitPortDirection) {
			return visitor.visitPortDirection(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class TransitionHandlerContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public transition(): TransitionContext {
		return this.getTypedRuleContext(TransitionContext, 0) as TransitionContext;
	}
	public HANDLER(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.HANDLER, 0);
	}
	public STRING(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.STRING, 0);
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_transitionHandler;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitTransitionHandler) {
			return visitor.visitTransitionHandler(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class AdditionalTransitionHandlerContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public transitionHandler(): TransitionHandlerContext {
		return this.getTypedRuleContext(TransitionHandlerContext, 0) as TransitionHandlerContext;
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_additionalTransitionHandler;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitAdditionalTransitionHandler) {
			return visitor.visitAdditionalTransitionHandler(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class TransitionGuardContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public transition(): TransitionContext {
		return this.getTypedRuleContext(TransitionContext, 0) as TransitionContext;
	}
	public GUARD(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.GUARD, 0);
	}
	public STRING(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.STRING, 0);
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_transitionGuard;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitTransitionGuard) {
			return visitor.visitTransitionGuard(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class TransitionTimerContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public transition(): TransitionContext {
		return this.getTypedRuleContext(TransitionContext, 0) as TransitionContext;
	}
	public TIMER(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.TIMER, 0);
	}
	public CLOCK(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.CLOCK, 0);
	}
	public place(): PlaceContext {
		return this.getTypedRuleContext(PlaceContext, 0) as PlaceContext;
	}
	public CEL(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.CEL, 0);
	}
	public STRING(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.STRING, 0);
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_transitionTimer;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitTransitionTimer) {
			return visitor.visitTransitionTimer(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class TransitionTimerBindContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public transition(): TransitionContext {
		return this.getTypedRuleContext(TransitionContext, 0) as TransitionContext;
	}
	public TIMER(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.TIMER, 0);
	}
	public BIND(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.BIND, 0);
	}
	public timerBindName(): TimerBindNameContext {
		return this.getTypedRuleContext(TimerBindNameContext, 0) as TimerBindNameContext;
	}
	public place(): PlaceContext {
		return this.getTypedRuleContext(PlaceContext, 0) as PlaceContext;
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_transitionTimerBind;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitTransitionTimerBind) {
			return visitor.visitTransitionTimerBind(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class TransitionTimerMaturityContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public transition(): TransitionContext {
		return this.getTypedRuleContext(TransitionContext, 0) as TransitionContext;
	}
	public TIMER(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.TIMER, 0);
	}
	public MATURITY(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.MATURITY, 0);
	}
	public CEL(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.CEL, 0);
	}
	public STRING(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.STRING, 0);
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_transitionTimerMaturity;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitTransitionTimerMaturity) {
			return visitor.visitTransitionTimerMaturity(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class TimerBindNameContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public IDENT(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.IDENT, 0);
	}
	public CLOCK(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.CLOCK, 0);
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_timerBindName;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitTimerBindName) {
			return visitor.visitTimerBindName(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class TransitionPriorityContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public transition(): TransitionContext {
		return this.getTypedRuleContext(TransitionContext, 0) as TransitionContext;
	}
	public PRIORITY(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.PRIORITY, 0);
	}
	public nonnegativeInteger(): NonnegativeIntegerContext {
		return this.getTypedRuleContext(NonnegativeIntegerContext, 0) as NonnegativeIntegerContext;
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_transitionPriority;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitTransitionPriority) {
			return visitor.visitTransitionPriority(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class TransitionOrderContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public transition(): TransitionContext {
		return this.getTypedRuleContext(TransitionContext, 0) as TransitionContext;
	}
	public ORDER(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.ORDER, 0);
	}
	public positiveInteger(): PositiveIntegerContext {
		return this.getTypedRuleContext(PositiveIntegerContext, 0) as PositiveIntegerContext;
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_transitionOrder;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitTransitionOrder) {
			return visitor.visitTransitionOrder(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class ChainOrderContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public AT(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.AT, 0);
	}
	public name(): NameContext {
		return this.getTypedRuleContext(NameContext, 0) as NameContext;
	}
	public ORDER(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.ORDER, 0);
	}
	public positiveInteger(): PositiveIntegerContext {
		return this.getTypedRuleContext(PositiveIntegerContext, 0) as PositiveIntegerContext;
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_chainOrder;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitChainOrder) {
			return visitor.visitChainOrder(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class PlaceAcceptsContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public place(): PlaceContext {
		return this.getTypedRuleContext(PlaceContext, 0) as PlaceContext;
	}
	public ACCEPTS(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.ACCEPTS, 0);
	}
	public LBRACK(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.LBRACK, 0);
	}
	public color_list(): ColorContext[] {
		return this.getTypedRuleContexts(ColorContext) as ColorContext[];
	}
	public color(i: number): ColorContext {
		return this.getTypedRuleContext(ColorContext, i) as ColorContext;
	}
	public RBRACK(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.RBRACK, 0);
	}
	public COMMA_list(): TerminalNode[] {
	    	return this.getTokens(VelocitronPetriNetParser.COMMA);
	}
	public COMMA(i: number): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.COMMA, i);
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_placeAccepts;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitPlaceAccepts) {
			return visitor.visitPlaceAccepts(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class PlaceCapacityContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public place(): PlaceContext {
		return this.getTypedRuleContext(PlaceContext, 0) as PlaceContext;
	}
	public CAPACITY_PER_COLOR_KEY(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.CAPACITY_PER_COLOR_KEY, 0);
	}
	public jsonObject(): JsonObjectContext {
		return this.getTypedRuleContext(JsonObjectContext, 0) as JsonObjectContext;
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_placeCapacity;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitPlaceCapacity) {
			return visitor.visitPlaceCapacity(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class ArcWeightContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public AT(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.AT, 0);
	}
	public name(): NameContext {
		return this.getTypedRuleContext(NameContext, 0) as NameContext;
	}
	public WEIGHT(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.WEIGHT, 0);
	}
	public jsonValue(): JsonValueContext {
		return this.getTypedRuleContext(JsonValueContext, 0) as JsonValueContext;
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_arcWeight;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitArcWeight) {
			return visitor.visitArcWeight(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class ArcDataContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public AT(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.AT, 0);
	}
	public name(): NameContext {
		return this.getTypedRuleContext(NameContext, 0) as NameContext;
	}
	public DATA(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.DATA, 0);
	}
	public CEL(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.CEL, 0);
	}
	public STRING(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.STRING, 0);
	}
	public jsonValue(): JsonValueContext {
		return this.getTypedRuleContext(JsonValueContext, 0) as JsonValueContext;
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_arcData;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitArcData) {
			return visitor.visitArcData(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class ArcPredicateContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public AT(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.AT, 0);
	}
	public name(): NameContext {
		return this.getTypedRuleContext(NameContext, 0) as NameContext;
	}
	public PREDICATE(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.PREDICATE, 0);
	}
	public predicateKind(): PredicateKindContext {
		return this.getTypedRuleContext(PredicateKindContext, 0) as PredicateKindContext;
	}
	public STRING(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.STRING, 0);
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_arcPredicate;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitArcPredicate) {
			return visitor.visitArcPredicate(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class ArcCorrelateContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public AT(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.AT, 0);
	}
	public name(): NameContext {
		return this.getTypedRuleContext(NameContext, 0) as NameContext;
	}
	public CORRELATE(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.CORRELATE, 0);
	}
	public CEL(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.CEL, 0);
	}
	public STRING(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.STRING, 0);
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_arcCorrelate;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitArcCorrelate) {
			return visitor.visitArcCorrelate(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class PredicateKindContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public CEL(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.CEL, 0);
	}
	public HANDLER(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.HANDLER, 0);
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_predicateKind;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitPredicateKind) {
			return visitor.visitPredicateKind(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class PositiveIntegerContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public POSITIVE_INTEGER(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.POSITIVE_INTEGER, 0);
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_positiveInteger;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitPositiveInteger) {
			return visitor.visitPositiveInteger(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class NonnegativeIntegerContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public POSITIVE_INTEGER(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.POSITIVE_INTEGER, 0);
	}
	public ZERO(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.ZERO, 0);
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_nonnegativeInteger;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitNonnegativeInteger) {
			return visitor.visitNonnegativeInteger(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class InitialMarkingContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public MARKING(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.MARKING, 0);
	}
	public INITIAL(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.INITIAL, 0);
	}
	public place(): PlaceContext {
		return this.getTypedRuleContext(PlaceContext, 0) as PlaceContext;
	}
	public LEFT_ARROW(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.LEFT_ARROW, 0);
	}
	public markingValue(): MarkingValueContext {
		return this.getTypedRuleContext(MarkingValueContext, 0) as MarkingValueContext;
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_initialMarking;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitInitialMarking) {
			return visitor.visitInitialMarking(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class AdditionalInitialMarkingContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public initialMarking(): InitialMarkingContext {
		return this.getTypedRuleContext(InitialMarkingContext, 0) as InitialMarkingContext;
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_additionalInitialMarking;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitAdditionalInitialMarking) {
			return visitor.visitAdditionalInitialMarking(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class NamedMarkingContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public MARKING(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.MARKING, 0);
	}
	public name(): NameContext {
		return this.getTypedRuleContext(NameContext, 0) as NameContext;
	}
	public place(): PlaceContext {
		return this.getTypedRuleContext(PlaceContext, 0) as PlaceContext;
	}
	public LEFT_ARROW(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.LEFT_ARROW, 0);
	}
	public markingValue(): MarkingValueContext {
		return this.getTypedRuleContext(MarkingValueContext, 0) as MarkingValueContext;
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_namedMarking;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitNamedMarking) {
			return visitor.visitNamedMarking(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class MarkingValueContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public templateReference(): TemplateReferenceContext {
		return this.getTypedRuleContext(TemplateReferenceContext, 0) as TemplateReferenceContext;
	}
	public positiveInteger(): PositiveIntegerContext {
		return this.getTypedRuleContext(PositiveIntegerContext, 0) as PositiveIntegerContext;
	}
	public STAR(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.STAR, 0);
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_markingValue;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitMarkingValue) {
			return visitor.visitMarkingValue(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class MetadataDescriptionContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public metadataTarget(): MetadataTargetContext {
		return this.getTypedRuleContext(MetadataTargetContext, 0) as MetadataTargetContext;
	}
	public DESCRIPTION(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.DESCRIPTION, 0);
	}
	public STRING(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.STRING, 0);
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_metadataDescription;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitMetadataDescription) {
			return visitor.visitMetadataDescription(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class MetadataAnnotationContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public metadataTarget(): MetadataTargetContext {
		return this.getTypedRuleContext(MetadataTargetContext, 0) as MetadataTargetContext;
	}
	public ANNOTATION(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.ANNOTATION, 0);
	}
	public name(): NameContext {
		return this.getTypedRuleContext(NameContext, 0) as NameContext;
	}
	public jsonValue(): JsonValueContext {
		return this.getTypedRuleContext(JsonValueContext, 0) as JsonValueContext;
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_metadataAnnotation;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitMetadataAnnotation) {
			return visitor.visitMetadataAnnotation(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class MetadataTargetContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public NET(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.NET, 0);
	}
	public place(): PlaceContext {
		return this.getTypedRuleContext(PlaceContext, 0) as PlaceContext;
	}
	public transition(): TransitionContext {
		return this.getTypedRuleContext(TransitionContext, 0) as TransitionContext;
	}
	public AT(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.AT, 0);
	}
	public name(): NameContext {
		return this.getTypedRuleContext(NameContext, 0) as NameContext;
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_metadataTarget;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitMetadataTarget) {
			return visitor.visitMetadataTarget(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class ViewPositionContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public VIEW(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.VIEW, 0);
	}
	public name(): NameContext {
		return this.getTypedRuleContext(NameContext, 0) as NameContext;
	}
	public POSITION(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.POSITION, 0);
	}
	public viewTarget(): ViewTargetContext {
		return this.getTypedRuleContext(ViewTargetContext, 0) as ViewTargetContext;
	}
	public AT_KEYWORD(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.AT_KEYWORD, 0);
	}
	public jsonObject(): JsonObjectContext {
		return this.getTypedRuleContext(JsonObjectContext, 0) as JsonObjectContext;
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_viewPosition;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitViewPosition) {
			return visitor.visitViewPosition(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class ViewRouteContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public VIEW(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.VIEW, 0);
	}
	public name_list(): NameContext[] {
		return this.getTypedRuleContexts(NameContext) as NameContext[];
	}
	public name(i: number): NameContext {
		return this.getTypedRuleContext(NameContext, i) as NameContext;
	}
	public ROUTE(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.ROUTE, 0);
	}
	public AT(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.AT, 0);
	}
	public ORTHOGONAL(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.ORTHOGONAL, 0);
	}
	public jsonArray(): JsonArrayContext {
		return this.getTypedRuleContext(JsonArrayContext, 0) as JsonArrayContext;
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_viewRoute;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitViewRoute) {
			return visitor.visitViewRoute(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class ViewTargetContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public place(): PlaceContext {
		return this.getTypedRuleContext(PlaceContext, 0) as PlaceContext;
	}
	public transition(): TransitionContext {
		return this.getTypedRuleContext(TransitionContext, 0) as TransitionContext;
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_viewTarget;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitViewTarget) {
			return visitor.visitViewTarget(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class ExtensionsContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public EXTENSIONS(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.EXTENSIONS, 0);
	}
	public jsonObject(): JsonObjectContext {
		return this.getTypedRuleContext(JsonObjectContext, 0) as JsonObjectContext;
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_extensions;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitExtensions) {
			return visitor.visitExtensions(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class TemplateDefinitionContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public templateReference(): TemplateReferenceContext {
		return this.getTypedRuleContext(TemplateReferenceContext, 0) as TemplateReferenceContext;
	}
	public COLON(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.COLON, 0);
	}
	public color(): ColorContext {
		return this.getTypedRuleContext(ColorContext, 0) as ColorContext;
	}
	public jsonValue(): JsonValueContext {
		return this.getTypedRuleContext(JsonValueContext, 0) as JsonValueContext;
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_templateDefinition;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitTemplateDefinition) {
			return visitor.visitTemplateDefinition(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class AdditionalTemplateDefinitionContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public templateDefinition(): TemplateDefinitionContext {
		return this.getTypedRuleContext(TemplateDefinitionContext, 0) as TemplateDefinitionContext;
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_additionalTemplateDefinition;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitAdditionalTemplateDefinition) {
			return visitor.visitAdditionalTemplateDefinition(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class TemplateReferenceContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public DOLLAR(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.DOLLAR, 0);
	}
	public name(): NameContext {
		return this.getTypedRuleContext(NameContext, 0) as NameContext;
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_templateReference;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitTemplateReference) {
			return visitor.visitTemplateReference(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class ColorContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public name(): NameContext {
		return this.getTypedRuleContext(NameContext, 0) as NameContext;
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_color;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitColor) {
			return visitor.visitColor(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class NameContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public IDENT(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.IDENT, 0);
	}
	public STRING(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.STRING, 0);
	}
	public TIMER(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.TIMER, 0);
	}
	public CLOCK(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.CLOCK, 0);
	}
	public BIND(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.BIND, 0);
	}
	public PRIORITY(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.PRIORITY, 0);
	}
	public COMPOSITION(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.COMPOSITION, 0);
	}
	public USE(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.USE, 0);
	}
	public AS(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.AS, 0);
	}
	public WIRE(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.WIRE, 0);
	}
	public PORT(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.PORT, 0);
	}
	public INPUT(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.INPUT, 0);
	}
	public OUTPUT(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.OUTPUT, 0);
	}
	public DESCRIPTION(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.DESCRIPTION, 0);
	}
	public ANNOTATION(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.ANNOTATION, 0);
	}
	public EXTENSIONS(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.EXTENSIONS, 0);
	}
	public VIEW(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.VIEW, 0);
	}
	public POSITION(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.POSITION, 0);
	}
	public ROUTE(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.ROUTE, 0);
	}
	public AT_KEYWORD(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.AT_KEYWORD, 0);
	}
	public ORTHOGONAL(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.ORTHOGONAL, 0);
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_name;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitName) {
			return visitor.visitName(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class JsonValueContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public jsonObject(): JsonObjectContext {
		return this.getTypedRuleContext(JsonObjectContext, 0) as JsonObjectContext;
	}
	public jsonArray(): JsonArrayContext {
		return this.getTypedRuleContext(JsonArrayContext, 0) as JsonArrayContext;
	}
	public STRING(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.STRING, 0);
	}
	public NUMBER(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.NUMBER, 0);
	}
	public POSITIVE_INTEGER(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.POSITIVE_INTEGER, 0);
	}
	public ZERO(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.ZERO, 0);
	}
	public TRUE(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.TRUE, 0);
	}
	public FALSE(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.FALSE, 0);
	}
	public NULL(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.NULL, 0);
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_jsonValue;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitJsonValue) {
			return visitor.visitJsonValue(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class JsonObjectContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public LBRACE(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.LBRACE, 0);
	}
	public RBRACE(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.RBRACE, 0);
	}
	public STRING_list(): TerminalNode[] {
	    	return this.getTokens(VelocitronPetriNetParser.STRING);
	}
	public STRING(i: number): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.STRING, i);
	}
	public COLON_list(): TerminalNode[] {
	    	return this.getTokens(VelocitronPetriNetParser.COLON);
	}
	public COLON(i: number): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.COLON, i);
	}
	public jsonValue_list(): JsonValueContext[] {
		return this.getTypedRuleContexts(JsonValueContext) as JsonValueContext[];
	}
	public jsonValue(i: number): JsonValueContext {
		return this.getTypedRuleContext(JsonValueContext, i) as JsonValueContext;
	}
	public COMMA_list(): TerminalNode[] {
	    	return this.getTokens(VelocitronPetriNetParser.COMMA);
	}
	public COMMA(i: number): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.COMMA, i);
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_jsonObject;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitJsonObject) {
			return visitor.visitJsonObject(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}


export class JsonArrayContext extends ParserRuleContext {
	constructor(parser?: VelocitronPetriNetParser, parent?: ParserRuleContext, invokingState?: number) {
		super(parent, invokingState);
    	this.parser = parser;
	}
	public LBRACK(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.LBRACK, 0);
	}
	public RBRACK(): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.RBRACK, 0);
	}
	public jsonValue_list(): JsonValueContext[] {
		return this.getTypedRuleContexts(JsonValueContext) as JsonValueContext[];
	}
	public jsonValue(i: number): JsonValueContext {
		return this.getTypedRuleContext(JsonValueContext, i) as JsonValueContext;
	}
	public COMMA_list(): TerminalNode[] {
	    	return this.getTokens(VelocitronPetriNetParser.COMMA);
	}
	public COMMA(i: number): TerminalNode {
		return this.getToken(VelocitronPetriNetParser.COMMA, i);
	}
    public get ruleIndex(): number {
    	return VelocitronPetriNetParser.RULE_jsonArray;
	}
	// @Override
	public accept<Result>(visitor: VelocitronPetriNetVisitor<Result>): Result {
		if (visitor.visitJsonArray) {
			return visitor.visitJsonArray(this);
		} else {
			return visitor.visitChildren(this);
		}
	}
}
