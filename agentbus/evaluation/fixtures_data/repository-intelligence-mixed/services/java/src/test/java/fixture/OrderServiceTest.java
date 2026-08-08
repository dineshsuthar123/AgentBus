package fixture;

public class OrderServiceTest {
    public void testTotal() {
        assert new OrderService().total(2, 5) == 10;
    }
}
