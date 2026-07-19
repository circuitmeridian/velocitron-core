grammar VelocitronPetriNet;

document
    : netHeader?
      ( chain
      | placeDeclaration
      | transitionDeclaration
      | transitionHandler
      | transitionGuard
      | transitionTimer
      | transitionTimerBind
      | transitionTimerMaturity
      | transitionPriority
      | transitionOrder
      | chainOrder
      | placePort
      | placeAccepts
      | placeCapacity
      | arcWeight
      | arcData
      | arcPredicate
      | arcCorrelate
      | initialMarking
      | templateDefinition
      | namedMarking
      | metadataDescription
      | metadataAnnotation
      | viewPosition
      | viewRoute
      | extensions
      )* EOF
    | compositionHeader (compositionUse | compositionWire)* EOF
    ;

netHeader
    : NET name STRING?
    ;

compositionHeader
    : COMPOSITION name
    ;

compositionUse
    : USE STRING AS IDENT
    ;

compositionWire
    : WIRE IDENT DOT place ARROW IDENT DOT place
    ;

chain
    : chainHandle? chainNode (arcSegment chainNode)+
    ;

chainNode
    : place
    | transition
    ;

arcSegment
    : (HYPHEN color)? arcOperator
    ;

arcOperator
    : ARROW
    | READ_ARROW
    | INHIBIT_ARROW
    ;

additionalChain
    : chain
    ;

chainHandle
    : AT name COLON
    ;

place
    : LPAREN name RPAREN
    ;

transition
    : LBRACK name RBRACK
    ;

placeDeclaration
    : place
    ;

transitionDeclaration
    : transition
    ;

placePort
    : place PORT portDirection color
    ;

portDirection
    : INPUT
    | OUTPUT
    ;

transitionHandler
    : transition HANDLER STRING
    ;

additionalTransitionHandler
    : transitionHandler
    ;

transitionGuard
    : transition GUARD STRING
    ;

transitionTimer
    : transition TIMER CLOCK place CEL STRING
    ;

transitionTimerBind
    : transition TIMER BIND timerBindName place
    ;

transitionTimerMaturity
    : transition TIMER MATURITY CEL STRING
    ;

timerBindName
    : IDENT
    | CLOCK
    ;

transitionPriority
    : transition PRIORITY nonnegativeInteger
    ;

transitionOrder
    : transition ORDER positiveInteger
    ;

chainOrder
    : AT name ORDER positiveInteger
    ;

placeAccepts
    : place ACCEPTS LBRACK color (COMMA color)* RBRACK
    ;

placeCapacity
    : place CAPACITY_PER_COLOR_KEY jsonObject
    ;

arcWeight
    : AT name WEIGHT jsonValue
    ;

arcData
    : AT name DATA CEL STRING
    | AT name DATA jsonValue
    ;

arcPredicate
    : AT name PREDICATE predicateKind STRING
    ;

arcCorrelate
    : AT name CORRELATE CEL STRING
    ;

predicateKind
    : CEL
    | HANDLER
    ;

positiveInteger
    : POSITIVE_INTEGER
    ;

nonnegativeInteger
    : POSITIVE_INTEGER
    | ZERO
    ;

initialMarking
    : MARKING INITIAL place LEFT_ARROW markingValue
    ;

additionalInitialMarking
    : initialMarking
    ;

namedMarking
    : MARKING name place LEFT_ARROW markingValue
    ;

markingValue
    : (positiveInteger STAR)? templateReference
    | positiveInteger
    ;

metadataDescription
    : metadataTarget DESCRIPTION STRING
    ;

metadataAnnotation
    : metadataTarget ANNOTATION name jsonValue
    ;

metadataTarget
    : NET
    | place
    | transition
    | AT name
    ;

viewPosition
    : VIEW name POSITION viewTarget AT_KEYWORD jsonObject
    ;

viewRoute
    : VIEW name ROUTE AT name ORTHOGONAL jsonArray
    ;

viewTarget
    : place
    | transition
    ;

extensions
    : EXTENSIONS jsonObject
    ;

templateDefinition
    : templateReference COLON color jsonValue
    ;

additionalTemplateDefinition
    : templateDefinition
    ;

templateReference
    : DOLLAR name
    ;

color
    : name
    ;

name
    : IDENT
    | STRING
    | TIMER
    | CLOCK
    | BIND
    | PRIORITY
    | COMPOSITION
    | USE
    | AS
    | WIRE
    | PORT
    | INPUT
    | OUTPUT
    | DESCRIPTION
    | ANNOTATION
    | EXTENSIONS
    | VIEW
    | POSITION
    | ROUTE
    | AT_KEYWORD
    | ORTHOGONAL
    ;

jsonValue
    : jsonObject
    | jsonArray
    | STRING
    | NUMBER
    | POSITIVE_INTEGER
    | ZERO
    | TRUE
    | FALSE
    | NULL
    ;

jsonObject
    : LBRACE (STRING COLON jsonValue (COMMA STRING COLON jsonValue)*)? RBRACE
    ;

jsonArray
    : LBRACK (jsonValue (COMMA jsonValue)*)? RBRACK
    ;

NET
    : 'net'
    ;

COMPOSITION
    : 'composition'
    ;

USE
    : 'use'
    ;

AS
    : 'as'
    ;

WIRE
    : 'wire'
    ;

PORT
    : 'port'
    ;

INPUT
    : 'input'
    ;

OUTPUT
    : 'output'
    ;

HANDLER
    : 'handler'
    ;

GUARD
    : 'guard'
    ;

ORDER
    : 'order'
    ;

TIMER
    : 'timer'
    ;

CLOCK
    : 'clock'
    ;

BIND
    : 'bind'
    ;

MATURITY
    : 'maturity'
    ;

PRIORITY
    : 'priority'
    ;

ACCEPTS
    : 'accepts'
    ;

CAPACITY_PER_COLOR_KEY
    : 'capacityPerColorKey'
    ;

WEIGHT
    : 'weight'
    ;

DATA
    : 'data'
    ;

PREDICATE
    : 'predicate'
    ;

CEL
    : 'cel'
    ;

CORRELATE
    : 'correlate'
    ;

MARKING
    : 'marking'
    ;

INITIAL
    : 'initial'
    ;

DESCRIPTION
    : 'description'
    ;

ANNOTATION
    : 'annotation'
    ;

EXTENSIONS
    : 'extensions'
    ;

VIEW
    : 'view'
    ;

POSITION
    : 'position'
    ;

ROUTE
    : 'route'
    ;

AT_KEYWORD
    : 'at'
    ;

ORTHOGONAL
    : 'orthogonal'
    ;

TRUE
    : 'true'
    ;

FALSE
    : 'false'
    ;

NULL
    : 'null'
    ;


AT
    : '@'
    ;
LPAREN
    : '('
    ;

RPAREN
    : ')'
    ;

LBRACK
    : '['
    ;

RBRACK
    : ']'
    ;

LBRACE
    : '{'
    ;

RBRACE
    : '}'
    ;

HYPHEN
    : '-'
    ;

READ_ARROW
    : '->?'
    ;

INHIBIT_ARROW
    : '->0'
    ;

ARROW
    : '->'
    ;

LEFT_ARROW
    : '<-'
    ;

DOLLAR
    : '$'
    ;

STAR
    : '*'
    ;

COLON
    : ':'
    ;

COMMA
    : ','
    ;

DOT
    : '.'
    ;

POSITIVE_INTEGER
    : [1-9] [0-9]*
    ;

ZERO
    : '0'
    ;


NUMBER
    : '-'? ('0' | [1-9] [0-9]*) ('.' [0-9]+)? ([eE] [+-]? [0-9]+)?
    ;

STRING
    : '"' (ESCAPE | ~["\\\u0000-\u001F])* '"'
    ;

IDENT
    : [A-Za-z_] [A-Za-z0-9_]*
    ;

LINE_COMMENT
    : '//' ~[\r\n]* -> skip
    ;

BLOCK_COMMENT
    : '/*' .*? '*/' -> skip
    ;

WS
    : [ \t\n]+ -> skip
    ;

CRLF
    : '\r\n' -> skip
    ;

UNSUPPORTED
    : .
    ;

fragment ESCAPE
    : '\\' (["\\/bfnrt] | 'u' HEX_DIGIT HEX_DIGIT HEX_DIGIT HEX_DIGIT)
    ;

fragment HEX_DIGIT
    : [0-9a-fA-F]
    ;
