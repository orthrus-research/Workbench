package research.orthrus.axiom.materialhost;

import java.lang.reflect.Modifier;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Test;
import research.orthrus.axiom.materialhost.MaterialCallGate.GuestMembers;
import research.orthrus.axiom.materialhost.MaterialCallGate.Member;
import static org.junit.jupiter.api.Assertions.*;

/** Pure reviewed metadata, not compilation, definition or execution of source. */
class MaterialGuestMembersTest {
    private Member member(String name,String descriptor,boolean isStatic) {
        return new Member(name,descriptor,Modifier.PUBLIC|(isStatic?Modifier.STATIC:0));
    }
    @Test void fieldAndMethodNamesAreNotInterchangeableAdmission() {
        var guest=new GuestMembers(List.of(member("material","Ljava/lang/Object;",true)),
                List.of(member("helper","()Ljava/lang/Object;",true)));
        assertTrue(guest.admits("invoke","helper",true));
        assertFalse(guest.admits("getProperty","helper",true));
        assertTrue(guest.admits("getProperty","material",true));
        assertTrue(guest.admits("setProperty","material",true));
        assertFalse(guest.admits("invoke","material",true));
        assertFalse(guest.admits("unrecognized","material",true));
    }
    @Test void classReceiverCannotBorrowInstanceOrClassFallbackMembers() {
        var guest=new GuestMembers(List.of(member("value","I",false),member("forName","I",true)),
                List.of(member("helper","()I",false),member("forName","()Ljava/lang/String;",true)));
        assertTrue(guest.admits("invoke","helper",false));
        assertFalse(guest.admits("invoke","helper",true));
        assertTrue(guest.admits("getProperty","value",false));
        assertFalse(guest.admits("getProperty","value",true));
        assertFalse(guest.admits("invoke","forName",true));
        assertTrue(guest.admits("getProperty","forName",true));
    }
    @Test void memberDescriptorAndOwnershipMetadataAreImmutable() {
        var fields=new ArrayList<Member>();fields.add(member("value","I",true));
        var guest=new GuestMembers(fields,List.of());fields.clear();
        assertTrue(guest.admits("getProperty","value",true));
        assertEquals("I",guest.fields().getFirst().descriptor());
        assertThrows(UnsupportedOperationException.class,()->guest.fields().clear());
    }
}
