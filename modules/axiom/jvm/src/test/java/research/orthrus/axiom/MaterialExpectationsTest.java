package research.orthrus.axiom;

import org.junit.jupiter.api.Test;
import java.math.BigDecimal;
import java.util.*;
import static org.junit.jupiter.api.Assertions.*;

/** Result projection tests; native semantics are checked by installed execution. */
class MaterialExpectationsTest {
    private static final String MATERIAL="supersymmetry:example";
    private Map<String,Object> check(String kind,Object expected) {
        return new LinkedHashMap<>(Map.of("id","intent","material",MATERIAL,"kind",kind,"equals",expected));
    }
    private Map<String,Object> execution() {
        return new LinkedHashMap<>(Map.of("materials",List.of(Map.of("name",MATERIAL,"registryIdentity",true,"id",31000,
                        "properties",List.of("dust"),"flags",List.of("no_smelting"))),
                "vocabulary",Map.of("properties",List.of("dust","gem"),"flags",List.of("no_smelting"),"gt-prefix-items",List.of("dust","plate")),
                "lookups",List.of(Map.of("requested",MATERIAL,"resolved",MATERIAL,"exactIdentity",true)),
                "prefixItems",Map.of("phase","COMPLETE","forms",List.of(Map.of("material",MATERIAL,"prefix","plate","eligible",false,
                        "generated",List.of(),"selected",Map.of("empty",false,"item","minecraft:fixture"))))));
    }
    private Map<String,Object> evaluate(Map<String,Object> check,Map<String,Object> execution,boolean usable) {
        return MaterialExpectations.evaluate(MaterialExpectations.parse(List.of(check)),execution,usable);
    }
    private String status(Map<String,Object> result) {return Json.string(result.get("status"));}
    private Map<String,Object> form(String fact,boolean expected) {
        var check=check("form",expected);check.putAll(Map.of("family","gt-prefix-items","prefix","plate","fact",fact));return check;
    }
    @Test void generationEligibilityAndUnifierSelectionAreDifferentFacts() {
        assertEquals("matched",status(evaluate(form("generated",false),execution(),true)));
        assertEquals("matched",status(evaluate(form("eligible",false),execution(),true)));
        assertEquals("matched",status(evaluate(form("selected",true),execution(),true)));
        var mismatch=evaluate(form("generated",true),execution(),true);
        assertEquals("mismatch",status(mismatch));
        assertEquals("/execution/prefixItems/forms/0/generated",Json.object(Json.array(mismatch.get("checks")).getFirst()).get("evidencePointer"));
        assertEquals("developer-intent-not-native-validity",mismatch.get("meaning"));
    }
    @Test void incompleteNativeLifecycleDoesNotInventMissingForm() {
        var partial=execution();partial.put("prefixItems",Map.of("phase","FAILED","forms",List.of()));
        var result=evaluate(form("generated",true),partial,true);
        assertEquals("incomplete",status(result));
        assertEquals("not-evaluated",Json.object(Json.array(result.get("checks")).getFirst()).get("status"));
        assertEquals("incomplete",status(evaluate(form("generated",false),Map.of(),false)));
    }
    @Test void unknownFamilyPrefixAndPropertyAreNotNegativeObservations() {
        var unknown=form("generated",false);unknown.put("family","susy-custom-items");
        for(var check:List.of(unknown,form("ordinaryDrop",false))) {
            var result=evaluate(check,execution(),true);assertEquals("incomplete",status(result));
            assertEquals("unsupported",Json.object(Json.array(result.get("checks")).getFirst()).get("status"));
        }
        var property=check("property",false);property.put("key","addon_property");
        assertEquals("incomplete",status(evaluate(property,execution(),true)));
        unknown=form("generated",false);unknown.put("prefix","unknown");
        assertEquals("incomplete",status(evaluate(unknown,execution(),true)));
    }
    @Test void materialPropertiesFlagsAndIntegerValuesUseNativeObservations() {
        var property=check("property",true);property.put("key","dust");assertEquals("matched",status(evaluate(property,execution(),true)));
        property.put("key","gem");assertEquals("mismatch",status(evaluate(property,execution(),true)));
        var flag=check("flag",true);flag.put("key","no_smelting");assertEquals("matched",status(evaluate(flag,execution(),true)));
        var value=check("value",new BigDecimal("31000"));value.put("key","id");assertEquals("matched",status(evaluate(value,execution(),true)));
    }
    @Test void fallbackLookupCannotCreateTheRequestedRegistryIdentity() {
        var registration=check("registration",false);registration.put("material","other:example");
        assertEquals("matched",status(evaluate(registration,execution(),true)));
        var absent=form("generated",false);absent.put("material","other:example");
        assertEquals("incomplete",status(evaluate(absent,execution(),true)));
    }
    @Test void explicitNativeNullFormulaIsNotMissingObservationOrEmptyText() {
        var expected=check("value","");expected.put("key","formula");expected.put("equals",null);
        var execution=execution();
        var material=new LinkedHashMap<String,Object>(Json.object(Json.array(execution.get("materials")).getFirst()));
        material.put("formula",null);execution.put("materials",List.of(material));
        var matched=evaluate(expected,execution,true);
        assertEquals("matched",status(matched));
        var observation=Json.object(Json.array(matched.get("checks")).getFirst());
        assertTrue(observation.containsKey("observed"));assertNull(observation.get("observed"));
        assertEquals("/execution/materials/0/formula",observation.get("evidencePointer"));
        expected.put("equals","");assertEquals("mismatch",status(evaluate(expected,execution,true)));
        expected.put("equals",null);material.remove("formula");
        assertEquals("incomplete",status(evaluate(expected,execution,true)));
        expected.remove("equals");assertThrows(Failure.class,()->MaterialExpectations.parse(List.of(expected)));
    }
    @Test void emptyOrMissingNativeRowsDoNotInventAbsence() {
        var missing=execution();missing.put("prefixItems",Map.of("phase","COMPLETE","forms",List.of()));
        assertEquals("incomplete",status(evaluate(form("generated",false),missing,true)));
        assertEquals("not-requested",MaterialExpectations.evaluate(List.of(),Map.of(),false).get("status"));
        missing.put("prefixItems",Map.of("phase","COMPLETE","forms",List.of(Map.of("material",MATERIAL,"prefix","plate","selected",Map.of()))));
        assertEquals("incomplete",status(evaluate(form("selected",true),missing,true)));
    }
    @Test void malformedExpectationsRefuseBeforeNativeExecution() {
        var unknown=check("unknown",true);
        var stringBoolean=check("registration","true");
        var badNumber=check("value",1.5);badNumber.put("key","id");
        var wrongStone=form("generated",true);wrongStone.put("stone","stone");
        for(var value:List.of(unknown,stringBoolean,badNumber,wrongStone))assertThrows(Failure.class,()->MaterialExpectations.parse(List.of(value)));
        var duplicate=check("registration",true);assertThrows(Failure.class,()->MaterialExpectations.parse(List.of(duplicate,duplicate)));
    }
    @Test void oneUnsupportedCheckRemainsVisibleAlongsideARealMismatch() {
        var mismatch=form("generated",true);var unknown=form("generated",false);unknown.put("id","uncovered");unknown.put("family","uncovered");
        var result=MaterialExpectations.evaluate(MaterialExpectations.parse(List.of(mismatch,unknown)),execution(),true);
        assertEquals("incomplete",status(result));
        assertEquals(List.of("mismatch","unsupported"),Json.array(result.get("checks")).stream().map(Json::object).map(row->row.get("status")).toList());
    }
    @Test void fluidQueueAndStoredFluidAreDifferentNativeFacts() {
        var execution=execution();
        execution.put("deferredWork",Map.of("schema","axiom.native-deferred-material-work.v1","phase","FROZEN",
                "fluidStorageKeys",List.of("gregtech:liquid"),"fluids",List.of(Map.of("material",MATERIAL,"hasFluidProperty",true,
                        "queued",List.of(Map.of("key","gregtech:liquid")),"stored",List.of())),
                "prefixProcessing",List.of(Map.of("prefix","dust","pendingMaterials",List.of(MATERIAL)))));
        var fluid=check("fluid",true);fluid.putAll(Map.of("storageKey","gregtech:liquid","fact","queued"));
        assertEquals("matched",status(evaluate(fluid,execution,true)));
        fluid.put("fact","stored");assertEquals("mismatch",status(evaluate(fluid,execution,true)));
        fluid.put("storageKey","addon:key");assertEquals("incomplete",status(evaluate(fluid,execution,true)));
        var processing=check("processing",true);processing.putAll(Map.of("prefix","dust","fact","queued"));
        assertEquals("matched",status(evaluate(processing,execution,true)));
        assertEquals("incomplete",status(evaluate(processing,execution(),true)));
        processing.put("prefix","unknown");assertEquals("incomplete",status(evaluate(processing,execution,true)));
        processing.put("fact","executed");assertThrows(Failure.class,()->MaterialExpectations.parse(List.of(processing)));
    }
    @Test void absentFluidPropertyIsKnownButMissingRowsAndIncompleteExecutionAreNot() {
        var execution=execution();var fluid=check("fluid",false);fluid.putAll(Map.of("storageKey","gregtech:liquid","fact","queued"));
        var work=new LinkedHashMap<String,Object>(Map.of("schema","axiom.native-deferred-material-work.v1","phase","FROZEN",
                "fluidStorageKeys",List.of("gregtech:liquid"),"fluids",List.of(Map.of("material",MATERIAL,"hasFluidProperty",false))));
        execution.put("deferredWork",work);assertEquals("matched",status(evaluate(fluid,execution,true)));
        work.put("fluids",List.of());assertEquals("incomplete",status(evaluate(fluid,execution,true)));
        assertEquals("incomplete",status(evaluate(fluid,execution,false)));
    }
    @Test void unvisitedPrefixQueueIsNotAnEmptyNativeQueue() {
        var execution=execution();
        var work=new LinkedHashMap<String,Object>(Map.of("schema","axiom.native-deferred-material-work.v1","phase","FROZEN"));
        execution.put("deferredWork",work);
        var check=check("processing",false);check.putAll(Map.of("prefix","dust","fact","queued"));
        var result=evaluate(check,execution,true);
        assertEquals("incomplete",status(result));
        var row=Json.object(Json.array(result.get("checks")).getFirst());
        assertEquals("not-evaluated",row.get("status"));
        assertEquals("Native prefix-processing checkpoint was not observed",row.get("reason"));
        assertFalse(row.containsKey("observed"));
        work.put("prefixProcessing",List.of(Map.of("prefix","dust","pendingMaterials",List.of())));
        assertEquals("matched",status(evaluate(check,execution,true)));
        assertEquals("incomplete",status(evaluate(check,execution,false)));
        work.put("phase","CLOSED");
        assertEquals("incomplete",status(evaluate(check,execution,true)));
    }
    private Map<String,Object> propertyValue(String field,Object expected) {
        var result=check("property-value",expected);result.putAll(Map.of("key","tool","field",field));return result;
    }
    private Map<String,Object> scalarExecution(String type,Object actual) {
        return new LinkedHashMap<>(Map.of("materials",List.of(Map.of("name",MATERIAL,
            "propertyValues",Map.of("tool",Map.of("toolSpeed",Map.of("type",type,"value",actual))))),
            "vocabulary",Map.of("propertyValues",Map.of("tool",Map.of("toolSpeed",type)))));
    }
    @Test void nativePropertyScalarHasAnExactObservationPointer() {
        var result=evaluate(propertyValue("toolSpeed",new BigDecimal("4.25")),scalarExecution("float32",4.25f),true);
        assertEquals("matched",status(result));
        var value=Json.object(Json.array(result.get("checks")).getFirst());
        assertEquals("/execution/materials/0/propertyValues/tool/toolSpeed/value",value.get("evidencePointer"));
        assertEquals(4.25f,value.get("observed"));
        assertEquals("mismatch",status(evaluate(propertyValue("toolSpeed",5),scalarExecution("float32",4.25f),true)));
        assertEquals("matched",status(evaluate(propertyValue("toolSpeed",false),scalarExecution("boolean",false),true)));
        assertEquals("matched",status(evaluate(propertyValue("toolSpeed",0),scalarExecution("int32",0),true)));
    }
    @Test void unsupportedPropertyFieldAndMissingValueAreNotZeroOrAbsence() {
        var expected=propertyValue("toolSpeed",0);var execution=scalarExecution("float32",0.0f);
        var unknown=propertyValue("unknown",0);
        var result=evaluate(unknown,execution,true);
        assertEquals("unsupported",Json.object(Json.array(result.get("checks")).getFirst()).get("status"));
        unknown.put("key","addon");assertEquals("incomplete",status(evaluate(unknown,execution,true)));
        execution.put("materials",List.of(Map.of("name",MATERIAL,"propertyValues",Map.of())));
        result=evaluate(expected,execution,true);
        var row=Json.object(Json.array(result.get("checks")).getFirst());
        assertEquals("not-evaluated",row.get("status"));assertFalse(row.containsKey("observed"));
        assertEquals("incomplete",status(evaluate(expected,Map.of(),false)));
    }
    @Test void propertyFloatingPointSpecialsAreExplicitStringsNotInvalidJsonNumbers() {
        for(String value:List.of("NaN","Infinity","-Infinity")) {
            var result=evaluate(propertyValue("toolSpeed",value),scalarExecution("float32",value),true);
            assertEquals("matched",status(result));assertDoesNotThrow(()->Json.parse(Json.write(result)));
            assertEquals("mismatch",status(evaluate(propertyValue("toolSpeed",0),scalarExecution("float32",value),true)));
        }
        assertEquals("matched",status(evaluate(propertyValue("toolSpeed",0),scalarExecution("float32",-0.0f),true)),
                "Numeric intent equality does not assert sign bits; the observation separately retains raw bits");
    }
    @Test void scalarRequestsRejectMissingSelectorsNullAndNonJsonNumbers() {
        for(Object bad:List.of("", "arbitrary", Double.NaN, Double.POSITIVE_INFINITY))
            assertThrows(Failure.class,()->MaterialExpectations.parse(List.of(propertyValue("toolSpeed",bad))));
        var missing=propertyValue("toolSpeed",1);missing.remove("field");
        assertThrows(Failure.class,()->MaterialExpectations.parse(List.of(missing)));
        var nullable=propertyValue("toolSpeed",1);nullable.put("equals",null);
        assertThrows(Failure.class,()->MaterialExpectations.parse(List.of(nullable)));
        assertThrows(Failure.class,()->MaterialExpectations.parse(List.of(propertyValue("../speed",1))));
    }
}
